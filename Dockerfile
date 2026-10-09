# Use an official Python runtime as a parent image
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.lock .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.lock

# Copy the current directory contents into the container at /app
RUN useradd --create-home --uid 10001 pebble
COPY --chown=pebble:pebble *.py ./
COPY --chown=pebble:pebble cogs ./cogs
USER pebble

# Run bot.py when the container launches
CMD ["python", "bot.py"]
