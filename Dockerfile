# Deviates from REQUIREMENTS.md section 7.1's original suggestion to use
# Playwright's own base image: that image bundles a fixed Python (3.10 as
# of writing), and requirements.txt pins packages (e.g. rpds-py) that need
# 3.11+ -- a real incompatibility hit during the first deploy attempt, not
# a stylistic choice. `playwright install --with-deps` installs the same
# OS-level libraries the Playwright base image would have shipped, so a
# plain, version-controlled Python base plus that command is just as
# reliable and lets requirements.txt's actual pins decide the interpreter.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY mad_platform ./mad_platform

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn mad_platform.web.app:app --host 0.0.0.0 --port ${PORT}"]
