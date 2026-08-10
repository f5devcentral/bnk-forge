/**
 * Tests for OpenTofuErrorDisplay component
 *
 * Covers: renders parsed error messages, raw error output, Copy button,
 * Retry button, credential errors, provider errors, syntax errors,
 * generic fallback error, multiple error patterns.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { render } from '@/test/test-utils';
import { OpenTofuErrorDisplay } from '@/components/modules/OpenTofuErrorDisplay';

describe('OpenTofuErrorDisplay', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the full error output section with raw text', () => {
    render(<OpenTofuErrorDisplay error="some error occurred" />);

    expect(screen.getByText('Full Error Output')).toBeInTheDocument();
    // The error text appears in both parsed detail and raw output
    const errorElements = screen.getAllByText('some error occurred');
    expect(errorElements.length).toBeGreaterThanOrEqual(1);
  });

  it('renders Copy button for error output', () => {
    render(<OpenTofuErrorDisplay error="test error" />);

    expect(screen.getByRole('button', { name: /copy/i })).toBeInTheDocument();
  });

  it('Copy button exists and is interactive', async () => {
    const user = userEvent.setup();
    render(<OpenTofuErrorDisplay error="error to copy" />);

    const copyBtn = screen.getByRole('button', { name: /copy/i });
    expect(copyBtn).toBeInTheDocument();
    expect(copyBtn).toBeEnabled();

    // Clicking Copy should not throw even in test environment
    // (clipboard API may not be available in jsdom)
    await expect(user.click(copyBtn)).resolves.not.toThrow();
  });

  it('renders Retry button when onRetry is provided', () => {
    const onRetry = vi.fn();
    render(<OpenTofuErrorDisplay error="failed" onRetry={onRetry} />);

    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument();
  });

  it('calls onRetry when Retry button is clicked', async () => {
    const user = userEvent.setup();
    const onRetry = vi.fn();
    render(<OpenTofuErrorDisplay error="failed" onRetry={onRetry} />);

    await user.click(screen.getByRole('button', { name: /retry/i }));

    expect(onRetry).toHaveBeenCalledOnce();
  });

  it('does not render Retry button when onRetry is not provided', () => {
    render(<OpenTofuErrorDisplay error="failed" />);

    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument();
  });

  it('parses credential errors and shows suggestion', () => {
    const error = 'Error: no valid credential sources found for AWS provider';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('Cloud Credentials Not Configured')).toBeInTheDocument();
    expect(screen.getByText('Suggestion')).toBeInTheDocument();
  });

  it('parses provider configuration errors', () => {
    const error = 'Error: provider "aws" not found in registry';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('Provider Configuration Error')).toBeInTheDocument();
  });

  it('parses resource not found errors', () => {
    const error = 'Error: resource "aws_instance.main" does not exist';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('Resource Not Found')).toBeInTheDocument();
  });

  it('parses syntax errors with line number', () => {
    const error = 'Error: syntax error on line 42: unexpected token "}"';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('OpenTofu Syntax Error')).toBeInTheDocument();
    expect(screen.getByText('Line 42')).toBeInTheDocument();
  });

  it('parses missing variable errors', () => {
    const error = 'Error: no value for required variable "vpc_cidr"';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('Missing Required Variable')).toBeInTheDocument();
  });

  it('parses state lock errors as warning severity', () => {
    const error = 'Error: state file locked by another process';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('State File Locked')).toBeInTheDocument();
    expect(screen.getByText('warning')).toBeInTheDocument();
  });

  it('shows generic fallback for unrecognized errors', () => {
    const error = 'Something totally unexpected happened';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('OpenTofu Operation Failed')).toBeInTheDocument();
  });

  it('renders severity badge for each parsed error', () => {
    const error = 'Error: InvalidClientTokenId when calling the API';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('error')).toBeInTheDocument();
  });

  it('renders View Documentation link for credential errors', () => {
    const error = 'Error: no valid credential sources found';
    render(<OpenTofuErrorDisplay error={error} />);

    expect(screen.getByText('View Documentation')).toBeInTheDocument();
  });
});
