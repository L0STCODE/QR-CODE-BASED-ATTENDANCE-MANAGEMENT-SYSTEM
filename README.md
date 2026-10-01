# QR Code Based Attendance Management System

A web-based attendance management system developed with Flask and SQLite. Lecturers can create lecture sessions and display a QR code that students scan to record attendance. The prototype also includes separate lecturer and student login flows, course management, session expiry, attendance records, and report export functionality.

## Features

- Lecturer login and registration
- Student login
- Create lecture sessions
- Generate unique QR codes for attendance
- Student attendance submission
- Duplicate attendance prevention
- Session status and expiry handling
- Course management
- Student management
- Attendance dashboard and records
- CSV attendance report export
- LAN-friendly QR links for classroom demonstrations
- SQLite database

## Technologies

- Python
- Flask
- SQLite
- HTML / Jinja2
- CSS
- JavaScript
- `qrcode` / Pillow
- Werkzeug password hashing

## Project Structure

```text
qr-code-attendance-management-system/
├── app.py
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
├── templates/
└── static/
```

## Setup

### 1. Clone the repository

```bash
git clone https://github.com/YOUR-USERNAME/qr-code-attendance-management-system.git
cd qr-code-attendance-management-system
```

### 2. Create a virtual environment

```bash
python -m venv venv
```

Windows:

```bash
venv\Scripts\activate
```

macOS / Linux:

```bash
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment variables

Copy `.env.example` to `.env` and set a secret key. The application can also run without a `.env` file for a local demonstration.

### 5. Run the application

```bash
python app.py
```

The application runs on port `5000` by default. For a classroom/LAN demonstration, open it from another device using the computer's local IP address.

## Demo Data

The application seeds demo courses and three demo student accounts when the database is first created. The demo passwords are intended for local testing only.

## Notes

This repository contains the application source code but does not include the local SQLite database. Running the application creates a fresh local database.

This project was developed as a university Computer Science prototype. It is intended for demonstration and learning rather than production deployment.

## Future Improvements

- Production-grade authentication and authorization
- Cloud database support
- More detailed attendance analytics
- Email notifications
- Deployment to a public hosting platform
- Integration with an official student information system

## Author

Kudzai Mtonga

Computer Science
