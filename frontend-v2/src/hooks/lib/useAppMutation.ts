import {
  useMutation,
  type UseMutationOptions,
  type UseMutationResult,
} from '@tanstack/react-query';
import { notifyError } from '@/lib/notify';
import type { ParseApiErrorContext } from '@/lib/error-handler';

export interface AppMutationOptions<TData, TError, TVariables, TContext>
  extends UseMutationOptions<TData, TError, TVariables, TContext> {
  silent?: boolean;
  errorContext?: ParseApiErrorContext;
}

export function useAppMutation<
  TData = unknown,
  TError = Error,
  TVariables = void,
  TContext = unknown,
>(
  options: AppMutationOptions<TData, TError, TVariables, TContext>,
): UseMutationResult<TData, TError, TVariables, TContext> {
  const { silent, errorContext, onError, ...rest } = options;

  const wrappedOnError: UseMutationOptions<TData, TError, TVariables, TContext>['onError'] =
    onError ??
    (silent
      ? undefined
      : (error) => {
          notifyError(error, undefined, errorContext);
        });

  return useMutation<TData, TError, TVariables, TContext>({
    ...rest,
    onError: wrappedOnError,
  });
}
