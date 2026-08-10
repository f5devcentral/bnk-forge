import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { z } from 'zod';
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@/components/ui/form';
import { Input } from '@/components/ui/input';

const schema = z.object({
  email: z.string().email('Enter a valid email'),
});
type Values = z.infer<typeof schema>;

function Harness({ onSubmit = vi.fn() }: { onSubmit?: (v: Values) => void }) {
  const form = useForm<Values>({
    resolver: zodResolver(schema),
    defaultValues: { email: '' },
    mode: 'onSubmit',
  });
  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)}>
        <FormField
          control={form.control}
          name="email"
          render={({ field }) => (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <FormControl>
                <Input type="text" placeholder="you@example.com" {...field} />
              </FormControl>
              <FormDescription>We never share this.</FormDescription>
              <FormMessage />
            </FormItem>
          )}
        />
        <button type="submit">Submit</button>
      </form>
    </Form>
  );
}

describe('Form primitive', () => {
  it('renders label, input, and description with associated ids', () => {
    render(<Harness />);
    const input = screen.getByLabelText('Email');
    expect(input).toBeInTheDocument();
    expect(input).toHaveAttribute('aria-invalid', 'false');
    // description is referenced by aria-describedby
    const describedBy = input.getAttribute('aria-describedby');
    expect(describedBy).toBeTruthy();
    expect(screen.getByText('We never share this.').id).toBe(describedBy);
  });

  it('shows the validation message and sets aria-invalid on submit failure', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole('button', { name: 'Submit' }));
    const input = await screen.findByLabelText('Email');
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByText('Enter a valid email')).toBeInTheDocument();
    // aria-describedby now references both description AND message
    const describedBy = input.getAttribute('aria-describedby') ?? '';
    expect(describedBy.split(' ').length).toBe(2);
  });

  it('clears the validation state once a valid value is entered', async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);
    await user.click(screen.getByRole('button', { name: 'Submit' }));
    expect(await screen.findByText('Enter a valid email')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Email'), 'a@b.co');
    await user.click(screen.getByRole('button', { name: 'Submit' }));

    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit.mock.calls[0][0]).toEqual({ email: 'a@b.co' });
    expect(screen.queryByText('Enter a valid email')).not.toBeInTheDocument();
  });

  it('preserves field values when validation fails (does not reset)', async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const input = screen.getByLabelText('Email') as HTMLInputElement;
    await user.type(input, 'bad-input');
    await user.click(screen.getByRole('button', { name: 'Submit' }));
    // Wait for aria-invalid to flip after async validation
    await screen.findByRole('textbox', { name: 'Email' });
    expect(input).toHaveAttribute('aria-invalid', 'true');
    expect(input.value).toBe('bad-input');
  });
});
