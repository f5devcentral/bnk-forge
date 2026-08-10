#!/bin/bash

# BNK-Forge v2 E2E Test Runner
# Quick script to run E2E tests with common configurations

set -e

echo "🧪 BNK-Forge v2 E2E Test Runner"
echo "================================"
echo ""

# Check if we're in the right directory
if [ ! -f "package.json" ]; then
    echo "❌ Error: Must run from tests/e2e/ directory"
    exit 1
fi

# Check if dependencies installed
if [ ! -d "node_modules" ]; then
    echo "📦 Installing dependencies..."
    npm install
fi

# Check if Playwright installed
if [ ! -d "$HOME/Library/Caches/ms-playwright/chromium-1200" ]; then
    echo "🎭 Installing Playwright browsers..."
    npx playwright install chromium
fi

# Parse command line arguments
MODE=${1:-"headless"}

case $MODE in
    "watch" | "headed")
        echo "👀 Running tests in HEADED mode (watch execution)..."
        echo "   Tests will run with browser visible"
        echo "   Resources will be left for inspection (SKIP_CLEANUP=true)"
        echo ""
        SKIP_CLEANUP=true npm run test:headed
        ;;

    "debug")
        echo "🐛 Running tests in DEBUG mode (step-by-step)..."
        echo "   Use Playwright inspector to step through tests"
        echo "   Resources will be left for inspection (SKIP_CLEANUP=true)"
        echo ""
        SKIP_CLEANUP=true npm run test:debug
        ;;

    "no-cleanup")
        echo "🔍 Running tests with NO CLEANUP (resources left for inspection)..."
        echo "   Tests run headless, but resources are NOT destroyed"
        echo "   ⚠️  Remember to manually clean up AWS resources!"
        echo ""
        SKIP_CLEANUP=true npm test
        ;;

    "no-aws")
        echo "⚡ Running tests WITHOUT AWS verification (faster)..."
        echo "   Skips AWS SDK calls"
        echo "   Resources will be destroyed (cleanup enabled)"
        echo ""
        VERIFY_AWS=false npm test
        ;;

    "headless" | "full")
        echo "🚀 Running FULL E2E test (headless with cleanup)..."
        echo "   All resources will be destroyed after test"
        echo "   This will take 30-45 minutes"
        echo ""
        npm test
        ;;

    *)
        echo "❌ Unknown mode: $MODE"
        echo ""
        echo "Usage: ./run-test.sh [mode]"
        echo ""
        echo "Available modes:"
        echo "  watch       - Run with visible browser, no cleanup"
        echo "  debug       - Run with Playwright inspector, no cleanup"
        echo "  no-cleanup  - Run headless, leave resources for inspection"
        echo "  no-aws      - Run without AWS verification (faster)"
        echo "  headless    - Run full test with cleanup (default)"
        echo ""
        exit 1
        ;;
esac

echo ""
echo "✅ Test execution complete!"
echo ""
echo "📊 View test report:"
echo "   npm run test:report"
echo ""
echo "📁 Test results saved to:"
echo "   test-results/"
echo ""
