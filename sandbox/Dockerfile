FROM node:18-alpine

# Install dumb-init for proper signal handling
RUN apk add --no-cache dumb-init

# Use the existing 'node' user (uid/gid 1000) from the base image
# This avoids conflicts with existing user/group IDs

# Set working directory
WORKDIR /app/workspace

# Ensure workspace is owned by node user
RUN chown -R node:node /app

# Switch to non-root user
USER node

# No CMD - command is passed at runtime by docker run
