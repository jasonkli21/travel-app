export const RECOVERY_KEY: string;
export function bindShoppingTaskOwner(ownerKey: string | null, storage?: Storage): void;
export function readShoppingTaskRecovery(ownerKey: string | null, storage?: Storage): ShoppingTaskRecovery | null;
export function writeShoppingTaskRecovery(record: ShoppingTaskRecovery, storage?: Storage): void;
export function clearShoppingTaskRecovery(storage?: Storage): void;
export function phaseAfterTaskFailure(phase: string, error: unknown): { phase: string; discard: boolean };
export function phaseForTaskStatus(state: string): string;
export interface ShoppingTaskRecovery {
  version: 1;
  owner_key: string;
  command_id: string;
  create_body: Record<string, unknown>;
  phase: string;
  confirmation_id: string | null;
}
