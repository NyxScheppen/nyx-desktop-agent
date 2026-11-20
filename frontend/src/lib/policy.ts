/** Substitute a policy template once so user text cannot become another field. */
export function formatPolicyText(template: string, values: Readonly<Record<string, unknown>>): string {
  return template.replace(/\{(p\d+)\}/g, (_match, key: string) => {
    if (!Object.prototype.hasOwnProperty.call(values, key)) {
      throw new Error(`Missing policy template field: ${key}`);
    }
    return String(values[key]);
  });
}
