FROM python:3.9-slim

WORKDIR /app

# Keep Python output unbuffered (helps with logs)
ENV PYTHONUNBUFFERED=1

# Copy requirements first for better caching
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application files
COPY . .

# Expose the port
EXPOSE 8000

# Command to run the application
CMD ["python", "api.py"] 