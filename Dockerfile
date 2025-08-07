FROM python:3.8-slim

# Install required Python packages
RUN pip install irc

# Copy the bot script into the container
COPY leetbot.py /leetbot.py

# Set the working directory
WORKDIR /

# Expose the default IRC port
EXPOSE 6667

# Run the bot using environment variables
CMD ["python", "/leetbot.py"]

