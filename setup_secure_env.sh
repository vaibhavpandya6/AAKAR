#!/bin/bash
# Setup secure environment variables for a project

set -e

PROJECT_ID="$1"

if [ -z "$PROJECT_ID" ]; then
    echo "Usage: ./setup_secure_env.sh <project_id>"
    echo ""
    echo "Example: ./setup_secure_env.sh proj-123"
    exit 1
fi

SECURE_ENV_DIR="secure_env"
ENV_FILE="$SECURE_ENV_DIR/$PROJECT_ID.env"

# Create secure_env directory if it doesn't exist
if [ ! -d "$SECURE_ENV_DIR" ]; then
    echo "Creating $SECURE_ENV_DIR directory..."
    mkdir -p "$SECURE_ENV_DIR"
    chmod 700 "$SECURE_ENV_DIR"
fi

# Check if .env file already exists
if [ -f "$ENV_FILE" ]; then
    echo "⚠️  Environment file already exists: $ENV_FILE"
    read -p "Overwrite? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "Aborted."
        exit 0
    fi
fi

# Copy template
if [ -f "$SECURE_ENV_DIR/.env.template" ]; then
    cp "$SECURE_ENV_DIR/.env.template" "$ENV_FILE"
    echo "✅ Created $ENV_FILE from template"
else
    # Create minimal .env file
    cat > "$ENV_FILE" << 'EOF'
# Environment variables for generated code
# Add your credentials here

DATABASE_URL=postgresql://user:pass@localhost:5432/dbname
API_KEY=your-api-key-here
EOF
    echo "✅ Created minimal $ENV_FILE"
fi

# Set restrictive permissions
chmod 600 "$ENV_FILE"

echo ""
echo "📝 Next steps:"
echo "   1. Edit $ENV_FILE with your credentials"
echo "   2. Never commit this file to git"
echo "   3. Verify permissions: ls -la $ENV_FILE"
echo ""
echo "🔒 Security notes:"
echo "   - File permissions: 600 (owner read/write only)"
echo "   - Directory permissions: 700 (owner access only)"
echo "   - This directory should be in .gitignore"
