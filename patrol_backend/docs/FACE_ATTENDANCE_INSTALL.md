# Face Attendance Installation Guide (Live Server)

This guide is for GTMS `checkin_v4` / `checkout_v4` face attendance.

It includes the production issue you hit on Python 3.12:
- `face_recognition_models` imports `pkg_resources`
- `pkg_resources` needs `setuptools<81`

---

## 1) What this feature needs

- `dlib`
- `face-recognition`
- `face-recognition-models`
- `setuptools<81` (important for current `face_recognition_models==0.3.0`)

In GTMS:
- `User.face_photo` (enrollment photo)
- `User.face_encoding`
- `Location.is_face_attendance_enabled`
- endpoints:
  - `POST /dashboard/attendance/checkin_v4/`
  - `POST /dashboard/attendance/checkout_v4/`

---

## 2) System packages (Ubuntu/Debian)

```bash
sudo apt-get install -y \
  build-essential \
  cmake \
  libopenblas-dev \
  liblapack-dev \
  libjpeg-dev \
  zlib1g-dev \
  python3-dev \
  pkg-config
```

> Note: If `apt-get update` fails due to unrelated repo GPG keys (mysql/mongodb), either fix/disable those repos first, or continue if required packages are already installed.

---

## 2A) Windows prerequisites

Windows works, but package compatibility is stricter. Recommended:

- Use **Python 3.10 or 3.11** for easiest `dlib` support.
- Use a dedicated venv.
- Install **Microsoft C++ Build Tools** (Desktop development with C++) if wheel is unavailable.

If using Python 3.12 on Windows, expect more wheel/build issues.

---

## 3) Python environment (exact order)

From backend root:

```bash
cd /var/www/html/babu/c/czip/GTMS/backendnew/gtms/patrol_backend
source ../venv/bin/activate
python3 -m pip install --upgrade pip wheel
python3 -m pip install "setuptools<81"
python3 -m pip install -r requirements.txt
```

### Windows (PowerShell)

```powershell
cd C:\path\to\GTMS\backendnew\gtms\patrol_backend
python -m venv ..\venv
..\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip wheel
python -m pip install "setuptools<81"
python -m pip install -r requirements.txt
```

---

## 4) Face libs install

```bash
python3 -m pip install dlib
python3 -m pip install face-recognition
python3 -m pip install face-recognition-models==0.3.0
python3 -m pip install -r requirements-face-attendance.txt
```

### Windows notes for `dlib`

1. Try normal install first:

```powershell
python -m pip install dlib
```

2. If it fails, install C++ Build Tools and retry.
3. If still failing, use a matching prebuilt wheel for your exact:
   - Python version (`cp310` / `cp311`)
   - architecture (`win_amd64`)

Never install a wheel for a different Python tag (for example `cp310` on `cp312`).

If pip asks for git fallback:

```bash
python3 -m pip install git+https://github.com/ageitgey/face_recognition_models
```

---

## 5) Verify install (must pass)

```bash
python3 -c "import dlib, face_recognition, face_recognition_models; print('OK', dlib.__version__)"
```

### Windows verify

```powershell
python -c "import dlib, face_recognition, face_recognition_models; print('OK', dlib.__version__)"
```

Expected:
- prints `OK <dlib-version>`
- may show a `pkg_resources is deprecated` warning (safe for now)

If you still get:
`ModuleNotFoundError: No module named 'pkg_resources'`

run again:

```bash
python3 -m pip install --force-reinstall "setuptools<81"
```

then re-test.

---

## 6) DB + app steps

```bash
python3 manage.py migrate
python3 manage.py check
```

Restart app server (gunicorn/supervisor/systemd/runserver).

---

## 7) Enable and use in GTMS

1. Turn on `Location.is_face_attendance_enabled` for required locations.
2. Upload `face_photo` for each user (enrollment).
3. Mobile/web app should call:
   - `checkin_v4` and `checkout_v4`
   - with image + latitude + longitude (same as v3 payload style)

Behavior:
- If face flag OFF: v4 behaves like v3 (no face match required).
- If face flag ON: face match is required; non-match returns error.

---

## 8) Known issues and fixes

| Error | Fix |
|---|---|
| `No module named pkg_resources` | `python3 -m pip install --force-reinstall "setuptools<81"` |
| `Please install face_recognition_models` even though installed | install with same interpreter: `python3 -m pip install face-recognition-models==0.3.0` |
| `...whl is not a supported wheel on this platform` | wheel tag mismatch (e.g. cp310 wheel on py3.12). Install `dlib` normally via pip source build or matching wheel |
| `_dlib_pybind11` import error | broken dlib build; reinstall dlib after system deps |
| 503 from v4 when face enabled | face libs not importable in runtime env (wrong venv/service) |

---

## 9) Recommendation for live server

Pin these in deployment:
- `setuptools<81`
- `face-recognition==1.3.0`
- `face-recognition-models==0.3.0`

This keeps behavior stable across restarts and deployments.
