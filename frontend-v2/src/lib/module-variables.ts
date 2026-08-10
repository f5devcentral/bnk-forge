import type { JsonValue, ModuleLibrary } from '@/types';

export function getSensitiveModuleVariableNames(module?: ModuleLibrary | null): Set<string> {
  const names = new Set<string>();
  for (const variable of module?.variables || []) {
    if (variable.sensitive) {
      names.add(variable.name);
    }
  }
  return names;
}

export function formatModuleVariableValue(
  key: string,
  value: JsonValue,
  sensitiveVariableNames: Set<string>
): string {
  if (sensitiveVariableNames.has(key)) {
    return '••••••••';
  }

  if (typeof value === 'string') {
    return value;
  }

  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
