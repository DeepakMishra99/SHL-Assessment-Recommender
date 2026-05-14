FROM python:3.10-slim

# Fix: Update system packages to patch known vulnerabilities
USER root
RUN apt-get update && apt-get upgrade -y && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Set up the rest as before
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
RUN useradd -m -u 1000 user
WORKDIR /code
COPY --chown=user:user requirements.txt /code/requirements.txt
USER user
RUN pip install --no-cache-dir --user -r /code/requirements.txt
COPY --chown=user:user . /code
EXPOSE 7860
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "7860"]

