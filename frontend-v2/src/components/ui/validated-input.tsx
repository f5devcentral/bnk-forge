import * as React from 'react';
import { Input, InputProps } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { cn } from '@/lib/utils';
import { AlertCircle, CheckCircle2 } from 'lucide-react';
import { ValidationResult } from '@/lib/validators';

export interface ValidatedInputProps extends Omit<InputProps, 'onChange'> {
  label?: string;
  description?: string;
  required?: boolean;
  validator?: (value: string) => ValidationResult;
  value: string;
  onChange: (value: string) => void;
  onValidationChange?: (isValid: boolean) => void;
  validateOnChange?: boolean;
  validateOnBlur?: boolean;
  showValidIcon?: boolean;
}

export const ValidatedInput = React.forwardRef<HTMLInputElement, ValidatedInputProps>(
  (
    {
      label,
      description,
      required = false,
      validator,
      value,
      onChange,
      onValidationChange,
      validateOnChange = true,
      validateOnBlur = true,
      showValidIcon = false,
      className,
      ...props
    },
    ref
  ) => {
    const [error, setError] = React.useState<string | undefined>();
    const [touched, setTouched] = React.useState(false);
    const [isValid, setIsValid] = React.useState(false);

    const validate = React.useCallback(
      (val: string) => {
        if (!validator) {
          setIsValid(true);
          setError(undefined);
          onValidationChange?.(true);
          return;
        }

        const result = validator(val);
        setIsValid(result.isValid);
        setError(result.error);
        onValidationChange?.(result.isValid);
      },
      [validator, onValidationChange]
    );

    const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
      const newValue = e.target.value;
      onChange(newValue);

      if (validateOnChange && touched) {
        validate(newValue);
      }
    };

    const handleBlur = () => {
      setTouched(true);
      if (validateOnBlur) {
        validate(value);
      }
    };

    // Validate on mount if value is present
    React.useEffect(() => {
      if (value && validator) {
        validate(value);
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally only run on mount; runtime validation is handled by handleChange/handleBlur
    }, []);

    const hasError = touched && error;
    const showSuccess = touched && isValid && !error && showValidIcon;

    return (
      <div className="space-y-2">
        {label && (
          <Label className="text-sm font-medium">
            {label}
            {required && <span className="text-destructive ml-1">*</span>}
          </Label>
        )}

        <div className="relative">
          <Input
            ref={ref}
            value={value}
            onChange={handleChange}
            onBlur={handleBlur}
            className={cn(
              className,
              hasError && 'border-destructive focus-visible:ring-destructive',
              showSuccess && 'border-success focus-visible:ring-success'
            )}
            aria-invalid={hasError ? 'true' : 'false'}
            aria-describedby={hasError ? `${props.id}-error` : description ? `${props.id}-description` : undefined}
            {...props}
          />

          {/* Success Icon */}
          {showSuccess && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
              <CheckCircle2 className="h-4 w-4 text-success" />
            </div>
          )}

          {/* Error Icon */}
          {hasError && (
            <div className="absolute right-3 top-1/2 -translate-y-1/2 pointer-events-none">
              <AlertCircle className="h-4 w-4 text-destructive" />
            </div>
          )}
        </div>

        {/* Description */}
        {description && !hasError && (
          <p id={`${props.id}-description`} className="text-sm text-muted-foreground">
            {description}
          </p>
        )}

        {/* Error Message */}
        {hasError && (
          <p id={`${props.id}-error`} className="text-sm text-destructive flex items-center gap-1">
            <AlertCircle className="h-3.5 w-3.5" />
            {error}
          </p>
        )}
      </div>
    );
  }
);

ValidatedInput.displayName = 'ValidatedInput';
