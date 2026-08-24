import "server-only";
import { createClient, type SupabaseClient } from "@supabase/supabase-js";
import { getAdminEnv } from "./env";

let serviceClient: SupabaseClient | undefined;

export function createSupabaseServiceClient(): SupabaseClient {
  if (serviceClient) return serviceClient;
  const env = getAdminEnv();
  serviceClient = createClient(env.supabaseUrl, env.supabaseServiceRoleKey, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
  return serviceClient;
}

export function createSupabaseAuthClient(): SupabaseClient {
  const env = getAdminEnv();
  return createClient(env.supabaseUrl, env.supabaseAnonKey, {
    auth: { persistSession: false, autoRefreshToken: false, detectSessionInUrl: false },
  });
}

export class AdminDatabaseError extends Error {
  constructor(
    public readonly functionName: string,
    public readonly databaseCode: string | undefined,
    message: string,
  ) {
    super(message);
    this.name = "AdminDatabaseError";
  }
}

export async function callAdminRpc<T>(
  functionName: string,
  args: Record<string, unknown>,
): Promise<T> {
  const { data, error } = await createSupabaseServiceClient().rpc(functionName, args);
  if (error) {
    throw new AdminDatabaseError(functionName, error.code, error.message);
  }
  return data as T;
}
