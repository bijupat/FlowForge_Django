"""
Services for the Workflows app.
Handles dynamic processor loading, file validation, output generation, and cleanup.
"""
import os
import uuid
import shutil
import logging
import importlib
from datetime import datetime
from typing import Dict, Any, List, Tuple
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _
import pandas as pd

from apps.settings.models import ApplicationSetting
from .models import Workflow, WorkflowExecutionLog

logger = logging.getLogger('flowforge.workflows')


class ProcessorLoadError(Exception):
    """Raised when a processor module or function cannot be loaded."""
    pass


class ProcessorExecuteError(Exception):
    """Raised when a processor function fails during execution."""
    pass


class ProcessingError(ProcessorExecuteError):
    """
    Raised by processor modules (apps/processors/*.py) to surface a
    user-friendly error message. This is the exception the README's
    "Processor Development" section documents processors as raising.
    Subclassing ProcessorExecuteError means process_workflow()'s existing
    except (ProcessorLoadError, ProcessorExecuteError) branch already
    catches it and re-raises with the processor's original message intact.
    """
    pass


class FileValidatorService:
    """
    Validates uploaded files against the workflow's input configuration.
    Checks: extension, size, filename pattern, required columns.
    """

    @staticmethod
    def validate_file(file: UploadedFile, input_field_config: Dict[str, Any]) -> None:
        """
        Validate a single uploaded file against its configuration.

        Args:
            file: The uploaded file object.
            input_field_config: Dict with allowed_extensions, max_size_mb, filename_pattern, required_columns.

        Raises:
            ValidationError with user-friendly message if validation fails.
        """
        # Check extension
        allowed_extensions = input_field_config.get('allowed_extensions', [])
        if allowed_extensions:
            ext = file.name.split('.')[-1].lower() if '.' in file.name else ''
            if ext not in allowed_extensions:
                raise ValidationError(
                    _('File "%(filename)s" has an invalid extension. Allowed: %(allowed)s') % {
                        'filename': file.name,
                        'allowed': ', '.join(allowed_extensions),
                    }
                )

        # Check size
        max_size_mb = input_field_config.get('max_size_mb', 50)
        if file.size > max_size_mb * 1024 * 1024:
            raise ValidationError(
                _('File "%(filename)s" exceeds the maximum size of %(size)d MB.') % {
                    'filename': file.name,
                    'size': max_size_mb,
                }
            )

        # Check filename pattern
        filename_pattern = input_field_config.get('filename_pattern')
        if filename_pattern:
            import re
            if not re.match(filename_pattern, file.name):
                raise ValidationError(
                    _('File "%(filename)s" does not match the required pattern.') % {
                        'filename': file.name,
                    }
                )

        # Check required columns for CSV/XLSX
        required_columns = input_field_config.get('required_columns', [])
        if required_columns and file.name.lower().endswith(('.csv', '.xlsx')):
            FileValidatorService._check_columns(file, required_columns)

    @staticmethod
    def _check_columns(file: UploadedFile, required_columns: List[str]) -> None:
        """
        Check if the file contains the required columns.

        Args:
            file: The uploaded file.
            required_columns: List of required column names.
        """
        try:
            if file.name.lower().endswith('.csv'):
                df = pd.read_csv(file, nrows=0)
            elif file.name.lower().endswith('.xlsx'):
                df = pd.read_excel(file, nrows=0)
            else:
                return  # Not a tabular file

            missing = [col for col in required_columns if col not in df.columns]
            if missing:
                raise ValidationError(
                    _('File "%(filename)s" is missing required columns: %(cols)s') % {
                        'filename': file.name,
                        'cols': ', '.join(missing),
                    }
                )
        except ValidationError:
            raise
        except Exception as e:
            raise ValidationError(
                _('Could not read file "%(filename)s". Ensure it is a valid file.') % {
                    'filename': file.name,
                }
            )
        finally:
            # Reset file pointer
            file.seek(0)


class WorkflowProcessorService:
    """
    Service for dynamically loading and executing workflow processors.
    """

    @staticmethod
    def load_processor(workflow: Workflow) -> Tuple[Any, Any]:
        """
        Dynamically import the processor module and get the function.

        Returns:
            Tuple of (module, function).

        Raises:
            ProcessorLoadError on failure.
        """
        try:
            # Construct full module path: apps.processors.<module_name>
            full_module_path = f'apps.{workflow.processor_module}'
            logger.debug(f"Loading processor module: {full_module_path}")
            module = importlib.import_module(full_module_path)
        except ImportError as e:
            logger.error(f"Failed to import processor module {workflow.processor_module}: {e}")
            raise ProcessorLoadError(
                _(f"Could not load the processor module '{workflow.processor_module}'. Please contact administrator.")
            )

        func = getattr(module, workflow.processor_function, None)
        if func is None:
            logger.error(f"Processor function '{workflow.processor_function}' not found in {workflow.processor_module}")
            raise ProcessorLoadError(
                _(f"Processor function '{workflow.processor_function}' not found. Please contact administrator.")
            )

        if not callable(func):
            raise ProcessorLoadError(
                _(f"'{workflow.processor_function}' is not a callable function. Please contact administrator.")
            )

        return module, func

    @staticmethod
    def save_uploaded_file(file: UploadedFile, execution_dir: Path, prefix: str = '') -> str:
        """
        Save an uploaded file to the execution directory.

        Args:
            file: The uploaded file to save.
            execution_dir: Directory to save the file into.
            prefix: Optional filename prefix. Used when saving several
                files for the same field so identically-named uploads
                (e.g. two files both called "QC.csv") don't silently
                overwrite one another on disk.

        Returns the path of the saved file.
        """
        # Sanitize and save
        safe_name = f"{prefix}{file.name.replace(' ', '_')}"
        file_path = execution_dir / safe_name
        with open(file_path, 'wb+') as dest:
            for chunk in file.chunks():
                dest.write(chunk)
        return str(file_path)

    @staticmethod
    def process_workflow(
        workflow: Workflow,
        uploaded_files: Dict[str, Any],
        form_data: Dict[str, Any],
        request=None,
    ) -> Dict[str, str]:
        """
        Execute a workflow with the provided files and form data.

        Args:
            workflow: The Workflow instance.
            uploaded_files: Dict mapping input field name to either a
                single UploadedFile (regular file field) or a list of
                UploadedFile objects (field configured with "multiple": true).
            form_data: Dict of additional form field values.
            request: The HTTP request object (for IP logging).

        Returns:
            Dict mapping output key to output file path.

        Raises:
            ProcessorLoadError, ProcessorExecuteError, ValidationError
        """
        start_time = datetime.now()
        execution_id = uuid.uuid4().hex[:12]
        execution_dir = Path(settings.BASE_DIR) / 'uploads' / execution_id
        execution_dir.mkdir(parents=True, exist_ok=True)

        # Prepare output directory
        output_dir = Path(settings.BASE_DIR) / 'generated' / execution_id
        output_dir.mkdir(parents=True, exist_ok=True)

        input_paths = {}
        input_filenames = []

        try:
            # Save uploaded files. A field's value may be a single
            # UploadedFile or a list of them (multi-file fields), so
            # input_paths[field_name] ends up as either a single path
            # string or a list of path strings to match.
            for field_name, file_or_files in uploaded_files.items():
                if isinstance(file_or_files, (list, tuple)):
                    saved_paths = []
                    for index, single_file in enumerate(file_or_files):
                        path = WorkflowProcessorService.save_uploaded_file(
                            single_file, execution_dir, prefix=f'{index}_'
                        )
                        saved_paths.append(path)
                        input_filenames.append(single_file.name)
                    input_paths[field_name] = saved_paths
                else:
                    path = WorkflowProcessorService.save_uploaded_file(file_or_files, execution_dir)
                    input_paths[field_name] = path
                    input_filenames.append(file_or_files.name)

            # Load and execute processor
            module, func = WorkflowProcessorService.load_processor(workflow)

            # Call processor function with input paths, form data, and output directory
            result = func(
                input_files=input_paths,
                form_data=form_data,
                output_dir=str(output_dir),
            )

            if not isinstance(result, dict):
                raise ProcessorExecuteError(_('Processor did not return expected output format.'))

            # Validate output files exist
            output_files = {}
            for key, path in result.items():
                if not os.path.exists(path):
                    logger.error(f"Output file does not exist: {path}")
                    raise ProcessorExecuteError(_(f'Expected output file was not generated: {key}'))
                output_files[key] = path

            # Log successful execution
            duration = (datetime.now() - start_time).total_seconds()
            client_ip = ''
            if request:
                client_ip = request.META.get('REMOTE_ADDR', '0.0.0.0')

            output_filenames = [os.path.basename(p) for p in output_files.values()]
            WorkflowExecutionLog.log_success(
                user=None if not request else request.user,
                workflow=workflow,
                ip=client_ip,
                input_files=input_filenames,
                output_files=output_filenames,
                duration=duration,
            )

            logger.info(f"Workflow {workflow.slug} executed successfully in {duration:.2f}s")
            return output_files

        except (ProcessorLoadError, ProcessorExecuteError):
            raise
        except Exception as e:
            # Log failure
            duration = (datetime.now() - start_time).total_seconds()
            client_ip = ''
            if request:
                client_ip = request.META.get('REMOTE_ADDR', '0.0.0.0')
            error_msg = str(e)
            logger.error(f"Workflow execution failed: {error_msg}", exc_info=True)

            WorkflowExecutionLog.log_failure(
                user=None if not request else request.user,
                workflow=workflow,
                ip=client_ip,
                input_files=input_filenames,
                error_message=error_msg,
                duration=duration,
            )

            raise ProcessorExecuteError(_(f'Workflow execution failed: {error_msg}'))

        finally:
            # Cleanup input directory after processing (optional, keep for a while)
            pass


class OutputGeneratorService:
    """
    Service for generating output files from the processor results.
    In this implementation, the processor itself generates the files.
    This class provides helper methods for file serving and format detection.
    """

    CONTENT_TYPES = {
        'csv': 'text/csv',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'pdf': 'application/pdf',
    }

    @staticmethod
    def get_content_type(format_type: str) -> str:
        """Get the MIME content type for a given format."""
        return OutputGeneratorService.CONTENT_TYPES.get(
            format_type.lower(), 'application/octet-stream'
        )

    @staticmethod
    def get_download_filename(base_name: str, format_type: str) -> str:
        """Construct a filename for download."""
        ext = format_type.lower()
        return f'{base_name}.{ext}'

    @staticmethod
    def ensure_output_structure(output_config: list, generated_files: Dict[str, str]) -> Dict[str, Dict]:
        """
        Map generated file paths to output configuration entries for display.

        Returns:
            Dict mapping output key to dict with 'path', 'format', 'label', 'filename'.
        """
        result = {}
        for output_def in output_config:
            key = output_def.get('name')
            if key in generated_files:
                result[key] = {
                    'path': generated_files[key],
                    'format': output_def.get('format', '').upper(),
                    'label': output_def.get('label', key),
                    'filename': os.path.basename(generated_files[key]),
                }
        return result


class CleanupService:
    """
    Service for cleaning up old temporary files (uploads and generated).
    Can be called periodically or on-demand.
    """

    @staticmethod
    def clean_old_files():
        """
        Remove uploads and generated files older than the configured retention period.
        """
        retention_hours = ApplicationSetting.get_setting('FILE_CLEANUP_HOURS', default=24)

        from datetime import timedelta
        from django.utils import timezone

        cutoff = timezone.now() - timedelta(hours=retention_hours)

        # Clean uploads directory
        uploads_dir = Path(settings.BASE_DIR) / 'uploads'
        if uploads_dir.exists():
            for item in uploads_dir.iterdir():
                if item.is_dir():
                    # Check directory modification time
                    if timezone.datetime.fromtimestamp(item.stat().st_mtime) < cutoff:
                        try:
                            shutil.rmtree(item)
                            logger.info(f"Cleaned up upload directory: {item}")
                        except Exception as e:
                            logger.error(f"Failed to clean upload dir {item}: {e}")

        # Clean generated directory
        generated_dir = Path(settings.BASE_DIR) / 'generated'
        if generated_dir.exists():
            for item in generated_dir.iterdir():
                if item.is_dir():
                    if timezone.datetime.fromtimestamp(item.stat().st_mtime) < cutoff:
                        try:
                            shutil.rmtree(item)
                            logger.info(f"Cleaned up generated directory: {item}")
                        except Exception as e:
                            logger.error(f"Failed to clean generated dir {item}: {e}")