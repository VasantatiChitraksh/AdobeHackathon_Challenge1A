# Use a slim Python 3.10 image as the base, ensuring AMD64 platform
FROM --platform=linux/amd64 python:3.10-slim

# Set the working directory inside the container to /app
WORKDIR /app

# Install system dependencies for multilingual support and clean up apt lists
RUN apt-get update && apt-get install -y \
    locales \
    && rm -rf /var/lib/apt/lists/*

# Generate necessary locales for en_US.UTF-8 and ja_JP.UTF-8
RUN sed -i '/en_US.UTF-8/s/^# //g' /etc/locale.gen && \
    sed -i '/ja_JP.UTF-8/s/^# //g' /etc/locale.gen && \
    locale-gen

# Set environment variables for locale settings
ENV LANG en_US.UTF-8
ENV LANGUAGE en_US:en
ENV LC_ALL en_US.UTF-8

# Copy the Python dependency file and install them, avoiding cache to keep image size small
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy your Python application script into the working directory
COPY process_pdfs.py .

# Define the entrypoint for the container.
# This will execute your process_pdfs.py script, automatically passing
# /app/input as the source for PDFs and /app/output for the JSON results.
ENTRYPOINT ["python", "process_pdfs.py", "--input_dir", "/app/input", "--output_dir", "/app/output"]
