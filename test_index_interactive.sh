#!/bin/bash
# Test script to verify /index command fix in interactive mode

echo "Testing /index fix in interactive mode"
echo "========================================"
echo ""
echo "This will test:"
echo "1. Start chat agent"
echo "2. Use /index to index a PDF"
echo "3. Try to query the document"
echo ""
echo "Commands to run:"
echo "  /index data/PDF/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf"
echo "  /list"
echo "  what is this document about?"
echo ""
echo "Starting chat agent..."
echo ""

# Create a test input file
cat > /tmp/chat_test_input.txt << 'EOF'
/index data/PDF/Oil-and-Gas-Activity-Operations-Manual-1-10.pdf
/list
what is this document about?
/quit
EOF

# Run the chat agent with the test input
uv run gaia chat < /tmp/chat_test_input.txt

# Cleanup
rm /tmp/chat_test_input.txt
