# FlowForge

**FlowForge** is a secure, internal workflow automation platform designed to process CSV and XLSX files and produce CSV, XLSX, or PDF outputs according to configurable workflow definitions. It features robust authentication, dynamic workflow execution, and a modular processor architecture.

## Key Features

- **Email OTP Authentication** – Secure passwordless login using one-time passcodes sent via email.
- **Dynamic Workflow Execution** – Upload files and fill in form fields that adapt to each workflow’s configuration.
- **Modular Processors** – Drop-in Python modules that transform input files into generated outputs.
- **IP Whitelisting** – Restrict access to the application by IP address or CIDR range.
- **Session Management** – Configurable session timeouts to automatically log out idle users.
- **Admin Dashboard** – Manage users, workflows, permissions, and application settings through Django’s admin interface.
- **Execution Logging** – Every workflow run is logged with status, duration, and detailed error messages.
- **Bootstrap 5 UI** – Clean, responsive interface that works on desktops and tablets.

---

## Prerequisites

- **Python** 3.13 or higher
- **PostgreSQL** 16 (or higher)
- **Git** (to clone the repository)

---

## Installation

### 1. Clone the repository

    git clone https://github.com/your-org/flowforge.git
    cd flowforge

### 2. Create and activate a virtual environment

    python -m venv venv
    source venv/bin/activate   # On Windows: venv\Scripts\activate

### 3. Install dependencies

    pip install -r requirements.txt

### 4. Configure environment variables

Copy the example environment file and edit it with your own values:

    cp .env.example .env

Open `.env` and fill in the required settings:

| Variable                  | Description                                                                                                                                    | Example Value         |
| ------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | --------------------- |
| `SECRET_KEY`              | Django secret key – generate with `python -c 'from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())'` | `django-insecure-...` |
| `DEBUG`                   | Set to `True` for development only                                                                                                             | `True`                |
| `ALLOWED_HOSTS`           | Comma-separated list of hosts                                                                                                                  | `localhost,127.0.0.1` |
| `DB_NAME`                 | PostgreSQL database name                                                                                                                       | `flowforge`           |
| `DB_USER`                 | PostgreSQL username                                                                                                                            | `postgres`            |
| `DB_PASSWORD`             | PostgreSQL password                                                                                                                            | `your_password`       |
| `DB_HOST`                 | Database host                                                                                                                                  | `localhost`           |
| `DB_PORT`                 | Database port                                                                                                                                  | `5432`                |
| `EMAIL_HOST`              | SMTP server hostname                                                                                                                           | `smtp.gmail.com`      |
| `EMAIL_PORT`              | SMTP server port                                                                                                                               | `587`                 |
| `EMAIL_USE_TLS`           | Use TLS for SMTP                                                                                                                               | `True`                |
| `EMAIL_HOST_USER`         | SMTP username                                                                                                                                  | `noreply@example.com` |
| `EMAIL_HOST_PASSWORD`     | SMTP password (use app-specific password if 2FA is enabled)                                                                                    | `your_email_password` |
| `DEFAULT_FROM_EMAIL`      | Sender address for OTP emails                                                                                                                  | `noreply@example.com` |
| `SESSION_TIMEOUT_MINUTES` | Idle session timeout (minutes)                                                                                                                 | `30`                  |
| `OTP_EXPIRY_MINUTES`      | OTP validity period (minutes)                                                                                                                  | `5`                   |
| `FILE_CLEANUP_HOURS`      | Age of temp files before cleanup (hours)                                                                                                       | `24`                  |

### 5. Run database migrations

    python manage.py migrate

### 6. Create a superuser (admin account)

    python manage.py createsuperuser

Follow the prompts. The **email** will be your admin login identifier.

### 7. Collect static files

    python manage.py collectstatic --noinput

---

## Running the Application

### Development server

    python manage.py runserver

Open your browser and visit **http://localhost:8000**.

### Production deployment with Gunicorn

    gunicorn flowforge.wsgi:application --bind 0.0.0.0:8000 --workers 4

For HTTPS, place Gunicorn behind **Nginx** and configure SSL termination. A sample Nginx configuration:

    server {
        listen 443 ssl;
        server_name flowforge.yourcompany.com;

        ssl_certificate     /path/to/cert.pem;
        ssl_certificate_key /path/to/key.pem;

        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        }

        location /static/ {
            alias /path/to/flowforge/staticfiles/;
        }
    }

---

## Application Setup (First-time Admin)

1. **Log in** to the admin panel at `http://localhost:8000/admin/` using your superuser credentials.
2. **Add allowed IP addresses** under `Settings > Allowed IPs`. Enter your internal network ranges (e.g., `192.168.1.0/24`) and mark them as active.
3. **Configure application settings** under `Settings > Application Settings`. Adjust `session_timeout_minutes`, `otp_expiry_minutes`, and `file_cleanup_hours` as needed.
4. **Upload processor modules** to `apps/processors/` (see "Processor Development" below).
5. **Create workflows** under `Workflows > Workflows`:
   - Give the workflow a **name** (the `slug` will be auto-generated).
   - Define the **input_config** as a JSON list of fields (file uploads, text, dropdowns, etc.).
   - Specify the **processor_module** and **processor_function** (e.g., `apps.processors.merge_reports` and `process`).
   - Define the **output_config** as a JSON list of expected outputs.
   - Enable `is_active`.
6. **Assign workflow permissions** under `Workflows > Workflow Permissions`. Grant specific users access to the workflows they need.
7. **Create users** under `Accounts > Users`. Users will log in with their email and receive an OTP.

---

## Usage (End-user Flow)

1. Open the FlowForge URL and you will see the **login page**.
2. Enter your **registered email address** and click **“Send OTP”**.
3. Check your inbox for the 6-digit code and enter it on the **OTP verification page**.
4. On the **dashboard**, you will see a list of workflows assigned to you.
5. Click a workflow to open the **dynamic form**. Upload your files and fill in any additional fields.
6. Click **“Process Workflow”**.
7. Once processing completes, **download the generated output files** from the results page.

---

## Project Structure

    flowforge/
    ├── flowforge/                  # Django project root
    │   ├── __init__.py
    │   ├── settings.py             # Main settings (database, email, middleware)
    │   ├── urls.py                 # Root URL configuration
    │   ├── wsgi.py                 # WSGI entry point for production
    │   └── asgi.py                 # ASGI entry point (async-ready)
    ├── apps/                       # Django applications
    │   ├── accounts/               # Custom user model & OTP authentication
    │   │   ├── models.py           # User, OTP models
    │   │   ├── services.py         # EmailOTPService (generate, send, verify)
    │   │   ├── views.py            # Login, OTP verify, Logout views
    │   │   ├── forms.py            # EmailForm, OTPForm
    │   │   ├── urls.py
    │   │   ├── admin.py
    │   │   └── templates/accounts/
    │   │       ├── login.html
    │   │       └── otp_verify.html
    │   ├── core/                   # Dashboard & middleware
    │   │   ├── middleware.py       # IPWhitelistMiddleware, SessionTimeoutMiddleware
    │   │   ├── views.py            # DashboardView
    │   │   ├── urls.py
    │   │   └── templates/core/
    │   │       └── dashboard.html
    │   ├── workflows/              # Workflow execution engine
    │   │   ├── models.py           # Workflow, WorkflowPermission, WorkflowExecutionLog
    │   │   ├── services.py         # WorkflowProcessorService, FileValidatorService,
    │   │   │                       # OutputGeneratorService, CleanupService
    │   │   ├── views.py            # WorkflowExecuteView, WorkflowDownloadView
    │   │   ├── forms.py            # build_dynamic_form()
    │   │   ├── urls.py
    │   │   ├── admin.py
    │   │   └── templates/workflows/
    │   │       ├── workflow_form.html
    │   │       └── workflow_result.html
    │   ├── settings/               # IP whitelist & application settings
    │   │   ├── models.py           # AllowedIP, ApplicationSetting
    │   │   ├── admin.py
    │   │   └── views.py
    │   └── processors/             # Processor modules
    │       ├── merge_reports.py
    │       ├── convert_excel.py
    │       └── patient_statistics.py
    ├── templates/                  # Base templates & error pages
    │   ├── base.html
    │   ├── 403.html
    │   ├── 404.html
    │   └── 500.html
    ├── static/                     # CSS, JavaScript
    │   └── css/
    │       └── custom.css
    ├── logs/                       # Application logs
    ├── uploads/                    # Temporary uploaded files
    ├── generated/                  # Generated output files
    ├── manage.py                   # Django management script
    ├── requirements.txt            # Python dependencies
    ├── .env.example                # Environment variable template
    ├── venv/          # Virtual Environment Folder
    │   ├── Include/
    │   ├── Lib/
    │   ├── Scripts/
    │   └── pyvenv.cfg

---

## Processor Development

Creating a new processor is straightforward:

1.  Add a Python module to `apps/processors/`, e.g., `my_processor.py`.
2.  Implement a function with the signature:

        def process(input_files: dict, form_data: dict) -> dict:
            # input_files maps field names to temporary file paths
            # form_data contains all other form field values
            # Return a dict mapping output keys to generated file paths
            return {'my_output': '/absolute/path/to/output.xlsx'}

3.  Handle exceptions cleanly. Raise a custom `ProcessingError` from `apps.workflows.services` if you need to surface a user-friendly message.
4.  In the admin panel, create or edit a **Workflow** and set:
    - `processor_module` = `apps.processors.my_processor`
    - `processor_function` = `process`
5.  Map the output key (`my_output`) in the `output_config` JSON so the UI can offer a download link.

---

## Running Tests

    python manage.py test

Tests cover models, services, forms, and views across all apps.

---

## Security Considerations

- **Internal network only** – FlowForge is designed for intranet deployment. Do not expose it directly to the public internet.
- **IP whitelist** – Always configure the `AllowedIP` records to restrict access.
- **HTTPS** – Use SSL termination at the reverse proxy (Nginx) in production.
- **Environment variables** – Never commit `.env` to version control. The file is listed in `.gitignore`.
- **OTP expiry** – Keep `OTP_EXPIRY_MINUTES` short (3–5 minutes).
- **Session timeout** – Set `SESSION_TIMEOUT_MINUTES` according to your security policy.
- **Dependencies** – Regularly update packages with `pip list --outdated` and review security advisories.

---

## Troubleshooting

| Problem                         | Solution                                                                                                                                                               |
| ------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **OTP emails not sending**      | Verify `EMAIL_HOST`, `EMAIL_PORT`, `EMAIL_USE_TLS`, `EMAIL_HOST_USER`, `EMAIL_HOST_PASSWORD` in `.env`. Test with `python manage.py shell` and Django’s `send_mail()`. |
| **IP Forbidden (403)**          | Add your client IP or network CIDR to `Settings > Allowed IPs` in the admin panel.                                                                                     |
| **Session expires too quickly** | Increase `SESSION_TIMEOUT_MINUTES` in `.env` or via `Application Setting` in the admin panel.                                                                          |
| **Static files missing (404)**  | Run `python manage.py collectstatic` and ensure Nginx (or WhiteNoise) is serving the `STATIC_ROOT` directory.                                                          |
| **File processing errors**      | Check the `logs/` directory for detailed tracebacks. Ensure input files match the expected format and column requirements.                                             |
| **Database connection refused** | Confirm PostgreSQL is running and the credentials in `.env` match your database.                                                                                       |

---

## Contributing

Contributions are welcome! Please follow these guidelines:

- Write code that conforms to **PEP 8**.
- Include **tests** for new features or bug fixes.
- Describe your changes clearly in the pull request.
- Keep processor modules self-contained and well-documented.

---

## License

This project is licensed under a proprietary/internal license. See the `LICENSE` file for details.

---

## Acknowledgements

FlowForge is built with:

- [Django](https://www.djangoproject.com/) – The web framework for perfectionists with deadlines.
- [Bootstrap 5](https://getbootstrap.com/) – Responsive front-end toolkit.
- [Pandas](https://pandas.pydata.org/) – Data analysis and manipulation.
- [OpenPyXL](https://openpyxl.readthedocs.io/) – Excel file handling.
- [ReportLab](https://www.reportlab.com/) – PDF generation.
- [Pillow](https://python-pillow.org/) – Image processing.
- [WhiteNoise](http://whitenoise.evans.io/) – Static file serving.
- [Gunicorn](https://gunicorn.org/) – Production WSGI server.
- [PostgreSQL](https://www.postgresql.org/) – Robust database system.
