# Dockerfile for running E2E tests in container
FROM mcr.microsoft.com/playwright:v1.40.0-jammy

# Set working directory
WORKDIR /tests

# Copy package files
COPY package.json package-lock.json* ./

# Install dependencies
RUN npm ci

# Install Playwright browsers
RUN npx playwright install chromium

# Copy test files
COPY . .

# Set environment variables for container execution
ENV CI=true
ENV HEADLESS=true

# Default command runs all tests
CMD ["npm", "test"]
