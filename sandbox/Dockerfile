FROM node:18-alpine

# Install dumb-init for proper signal handling + Python, build tools, common utilities
RUN apk add --no-cache \
    dumb-init \
    python3 \
    py3-pip \
    git \
    curl \
    wget \
    bash \
    jq \
    build-base \
    libffi-dev \
    openssl-dev

# Create node user's home directory
RUN mkdir -p /home/node && chown -R node:node /home/node

# Install common npm packages globally
RUN npm install -g \
    express \
    axios \
    lodash \
    moment \
    dotenv

# Install common Python packages
RUN pip3 install --no-cache-dir \
    requests \
    flask \
    pytest

# Set working directory
WORKDIR /app/workspace

# Ensure workspace is owned by node user
RUN chown -R node:node /app

# Switch to non-root user
USER node

# Environment variables
ENV HOME=/home/node
ENV PYTHONPATH=/home/node/.local/lib/python3.11/site-packages

# No CMD - command is passed at runtime by docker run
