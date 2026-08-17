FROM python:3.12-alpine
WORKDIR /app
COPY anchor.py .
EXPOSE 8586
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD pgrep -f anchor.py || exit 1
ENTRYPOINT ["python3", "-u", "anchor.py"]
