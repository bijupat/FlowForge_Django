"""
Forms for the Workflows app.
Provides a factory function to generate dynamic Django forms based on workflow input_config.
"""
from django import forms
from django.utils.translation import gettext_lazy as _

FIELD_TYPE_MAP = {
    'text': forms.CharField,
    'file': forms.FileField,
    'date': forms.DateField,
    'checkbox': forms.BooleanField,
    'dropdown': forms.ChoiceField,
    'hidden': forms.CharField,
    'readonly': forms.CharField,
    'number': forms.FloatField,
}


class MultipleFileInput(forms.ClearableFileInput):
    """
    File widget that allows selecting more than one file in the browser's
    file picker. ClearableFileInput doesn't do this by default; setting
    allow_multiple_selected is Django's own documented way to opt back in.
    """
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    """
    FileField that validates a list of uploaded files instead of a single
    file. Pairs with MultipleFileInput. clean() is called once per file so
    each one goes through FileField's normal validation individually.
    """

    def __init__(self, *args, **kwargs):
        kwargs.setdefault('widget', MultipleFileInput())
        super().__init__(*args, **kwargs)

    def clean(self, data, initial=None):
        single_file_clean = super().clean
        if isinstance(data, (list, tuple)):
            return [single_file_clean(item, initial) for item in data]
        return single_file_clean(data, initial)


def build_dynamic_form(input_config: list) -> type:
    """
    Factory function that creates a dynamic Django Form class
    based on the workflow's input_config JSON.

    Args:
        input_config: List of dicts defining form fields. A file field
            with "multiple": true will accept more than one file.

    Returns:
        A dynamically generated Form class.
    """
    fields = {}

    for field_def in input_config:
        field_name = field_def.get('name')
        if not field_name:
            continue  # Skip invalid entries

        label = field_def.get('label', field_name.replace('_', ' ').title())
        field_type = field_def.get('field_type', 'text')
        required = field_def.get('required', False)
        allow_multiple = bool(field_def.get('multiple', False))
        widget_attrs = {
            'class': 'form-control',
        }

        # Additional attributes for specific types
        if field_type == 'file':
            widget_attrs['class'] = 'form-control-file'
            allowed_extensions = field_def.get('allowed_extensions', [])
            accept = ','.join([f'.{ext}' for ext in allowed_extensions])
            if accept:
                widget_attrs['accept'] = accept
            if allow_multiple:
                widget_attrs['multiple'] = 'multiple'
        elif field_type == 'date':
            widget_attrs['type'] = 'date'
        elif field_type == 'hidden':
            widget = forms.HiddenInput(attrs=widget_attrs)
            fields[field_name] = forms.CharField(
                label=label,
                required=required,
                initial=field_def.get('default_value', ''),
                widget=widget,
            )
            continue
        elif field_type == 'readonly':
            widget = forms.TextInput(attrs={**widget_attrs, 'readonly': 'readonly'})
            fields[field_name] = forms.CharField(
                label=label,
                required=False,
                initial=field_def.get('default_value', ''),
                widget=widget,
            )
            continue
        elif field_type == 'number':
            widget_attrs['step'] = 'any'

        field_class = FIELD_TYPE_MAP.get(field_type, forms.CharField)
        widget = forms.TextInput(attrs=widget_attrs)

        if field_type == 'checkbox':
            widget = forms.CheckboxInput(attrs={'class': 'form-check-input'})
        elif field_type == 'file':
            if allow_multiple:
                field_class = MultipleFileField
                widget = MultipleFileInput(attrs=widget_attrs)
            else:
                widget = forms.ClearableFileInput(attrs=widget_attrs)
        elif field_type == 'dropdown':
            choices = field_def.get('options', [])
            # Ensure choices are in correct format
            formatted_choices = [(c, c) if not isinstance(c, (tuple, list)) else tuple(c) for c in choices]
            fields[field_name] = forms.ChoiceField(
                label=label,
                required=required,
                choices=formatted_choices,
                widget=forms.Select(attrs=widget_attrs),
            )
            continue

        default_value = field_def.get('default_value')
        initial = default_value if default_value else None

        field_kwargs = {
            'label': label,
            'required': required,
            'widget': widget,
        }
        if initial is not None:
            field_kwargs['initial'] = initial

        fields[field_name] = field_class(**field_kwargs)

    # Create the dynamic form class
    FormClass = type('DynamicWorkflowForm', (forms.Form,), fields)

    return FormClass