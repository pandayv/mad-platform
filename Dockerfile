# Plain Python base rather than Playwright's own image: Playwright's base
# image bundles a fixed Python version that can be older than what
# requirements.txt's pins need. `playwright install --with-deps` installs
# the same OS-level libraries that image would have shipped, so a plain,
# version-controlled Python base plus that command is just as reliable and
# lets requirements.txt's actual pins decide the interpreter.
FROM python:3.13-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    && python -m playwright install --with-deps chromium

COPY mad_platform ./mad_platform

ENV PORT=8080
EXPOSE 8080

CMD ["sh", "-c", "uvicorn mad_platform.web.app:app --host 0.0.0.0 --port ${PORT}"]
