export type Json =
  | string
  | number
  | boolean
  | null
  | { [key: string]: Json | undefined }
  | Json[]

export type Database = {
  // Allows to automatically instantiate createClient with right options
  // instead of createClient<Database, { PostgrestVersion: 'XX' }>(URL, KEY)
  __InternalSupabase: {
    PostgrestVersion: "14.5"
  }
  public: {
    Tables: {
      api_keys: {
        Row: {
          created_at: string
          id: string
          key_hash: string
          key_prefix: string
          label: string
          last_used_at: string | null
          revoked_at: string | null
          user_id: string
        }
        Insert: {
          created_at?: string
          id?: string
          key_hash: string
          key_prefix: string
          label?: string
          last_used_at?: string | null
          revoked_at?: string | null
          user_id: string
        }
        Update: {
          created_at?: string
          id?: string
          key_hash?: string
          key_prefix?: string
          label?: string
          last_used_at?: string | null
          revoked_at?: string | null
          user_id?: string
        }
        Relationships: []
      }
      challenge_results: {
        Row: {
          alt_audio_bytes: number | null
          alt_audio_mime: string | null
          alt_audio_path: string | null
          alt_audio_url: string | null
          alt_error: string | null
          alt_model: string | null
          alt_ms: number | null
          alt_rated_at: string | null
          alt_rating: number | null
          alt_voice: string | null
          audio_bucket: string | null
          audio_bytes: number | null
          audio_mime: string | null
          audio_path: string | null
          audio_url: string | null
          coda_audio_bytes: number | null
          coda_audio_mime: string | null
          coda_audio_path: string | null
          coda_audio_url: string | null
          coda_error: string | null
          coda_model: string | null
          coda_ms: number | null
          coda_rated_at: string | null
          coda_rating: number | null
          coda_voice: string | null
          created_at: string
          duration_seconds: number | null
          filename: string | null
          id: string
          lang: string
          metadata: Json
          transcript: string
          transcript_confidence: number | null
          transcript_error: string | null
          transcript_language_code: string | null
          transcript_model: string | null
          transcript_ms: number | null
          updated_at: string
          user_id: string
        }
        Insert: {
          alt_audio_bytes?: number | null
          alt_audio_mime?: string | null
          alt_audio_path?: string | null
          alt_audio_url?: string | null
          alt_error?: string | null
          alt_model?: string | null
          alt_ms?: number | null
          alt_rated_at?: string | null
          alt_rating?: number | null
          alt_voice?: string | null
          audio_bucket?: string | null
          audio_bytes?: number | null
          audio_mime?: string | null
          audio_path?: string | null
          audio_url?: string | null
          coda_audio_bytes?: number | null
          coda_audio_mime?: string | null
          coda_audio_path?: string | null
          coda_audio_url?: string | null
          coda_error?: string | null
          coda_model?: string | null
          coda_ms?: number | null
          coda_rated_at?: string | null
          coda_rating?: number | null
          coda_voice?: string | null
          created_at?: string
          duration_seconds?: number | null
          filename?: string | null
          id?: string
          lang?: string
          metadata?: Json
          transcript?: string
          transcript_confidence?: number | null
          transcript_error?: string | null
          transcript_language_code?: string | null
          transcript_model?: string | null
          transcript_ms?: number | null
          updated_at?: string
          user_id: string
        }
        Update: {
          alt_audio_bytes?: number | null
          alt_audio_mime?: string | null
          alt_audio_path?: string | null
          alt_audio_url?: string | null
          alt_error?: string | null
          alt_model?: string | null
          alt_ms?: number | null
          alt_rated_at?: string | null
          alt_rating?: number | null
          alt_voice?: string | null
          audio_bucket?: string | null
          audio_bytes?: number | null
          audio_mime?: string | null
          audio_path?: string | null
          audio_url?: string | null
          coda_audio_bytes?: number | null
          coda_audio_mime?: string | null
          coda_audio_path?: string | null
          coda_audio_url?: string | null
          coda_error?: string | null
          coda_model?: string | null
          coda_ms?: number | null
          coda_rated_at?: string | null
          coda_rating?: number | null
          coda_voice?: string | null
          created_at?: string
          duration_seconds?: number | null
          filename?: string | null
          id?: string
          lang?: string
          metadata?: Json
          transcript?: string
          transcript_confidence?: number | null
          transcript_error?: string | null
          transcript_language_code?: string | null
          transcript_model?: string | null
          transcript_ms?: number | null
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      credit_balances: {
        Row: {
          balance: number
          lifetime_granted: number
          lifetime_spent: number
          updated_at: string
          user_id: string
        }
        Insert: {
          balance?: number
          lifetime_granted?: number
          lifetime_spent?: number
          updated_at?: string
          user_id: string
        }
        Update: {
          balance?: number
          lifetime_granted?: number
          lifetime_spent?: number
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      credit_ledger: {
        Row: {
          api_key_id: string | null
          balance_after: number
          created_at: string
          delta: number
          id: number
          metadata: Json | null
          reason: string
          ref: string | null
          user_id: string
        }
        Insert: {
          api_key_id?: string | null
          balance_after: number
          created_at?: string
          delta: number
          id?: never
          metadata?: Json | null
          reason: string
          ref?: string | null
          user_id: string
        }
        Update: {
          api_key_id?: string | null
          balance_after?: number
          created_at?: string
          delta?: number
          id?: never
          metadata?: Json | null
          reason?: string
          ref?: string | null
          user_id?: string
        }
        Relationships: []
      }
      credit_packs: {
        Row: {
          amount: number
          created_at: string
          credits: number
          currency: string
          id: string
          is_active: boolean
          name: string
          name_ar: string | null
          sort_order: number
        }
        Insert: {
          amount: number
          created_at?: string
          credits: number
          currency?: string
          id: string
          is_active?: boolean
          name: string
          name_ar?: string | null
          sort_order?: number
        }
        Update: {
          amount?: number
          created_at?: string
          credits?: number
          currency?: string
          id?: string
          is_active?: boolean
          name?: string
          name_ar?: string | null
          sort_order?: number
        }
        Relationships: []
      }
      demo_bookings: {
        Row: {
          company: string | null
          created_at: string
          email: string
          id: string
          message: string | null
          metadata: Json
          name: string
          phone: string
          preferred_date: string | null
          preferred_time: string | null
          source: string
          status: string
          updated_at: string
          user_id: string | null
        }
        Insert: {
          company?: string | null
          created_at?: string
          email: string
          id?: string
          message?: string | null
          metadata?: Json
          name: string
          phone: string
          preferred_date?: string | null
          preferred_time?: string | null
          source?: string
          status?: string
          updated_at?: string
          user_id?: string | null
        }
        Update: {
          company?: string | null
          created_at?: string
          email?: string
          id?: string
          message?: string | null
          metadata?: Json
          name?: string
          phone?: string
          preferred_date?: string | null
          preferred_time?: string | null
          source?: string
          status?: string
          updated_at?: string
          user_id?: string | null
        }
        Relationships: []
      }
      feedback: {
        Row: {
          audio_bucket: string | null
          audio_bytes: number | null
          audio_mime: string | null
          audio_path: string | null
          audio_url: string | null
          created_at: string
          duration_seconds: number | null
          id: string
          item_key: string
          lang: string | null
          metadata: Json
          prompt_text: string | null
          rating: number
          session_id: string | null
          source: string
          transcript: string
          turn_index: number | null
          updated_at: string
          user_id: string
          voice: string | null
        }
        Insert: {
          audio_bucket?: string | null
          audio_bytes?: number | null
          audio_mime?: string | null
          audio_path?: string | null
          audio_url?: string | null
          created_at?: string
          duration_seconds?: number | null
          id?: string
          item_key: string
          lang?: string | null
          metadata?: Json
          prompt_text?: string | null
          rating: number
          session_id?: string | null
          source: string
          transcript?: string
          turn_index?: number | null
          updated_at?: string
          user_id: string
          voice?: string | null
        }
        Update: {
          audio_bucket?: string | null
          audio_bytes?: number | null
          audio_mime?: string | null
          audio_path?: string | null
          audio_url?: string | null
          created_at?: string
          duration_seconds?: number | null
          id?: string
          item_key?: string
          lang?: string | null
          metadata?: Json
          prompt_text?: string | null
          rating?: number
          session_id?: string | null
          source?: string
          transcript?: string
          turn_index?: number | null
          updated_at?: string
          user_id?: string
          voice?: string | null
        }
        Relationships: []
      }
      payments: {
        Row: {
          amount: number
          checkout_url: string | null
          created_at: string
          credits: number
          currency: string
          fulfilled_at: string | null
          id: string
          kind: string
          pack_id: string | null
          plan_id: string | null
          provider: string
          provider_invoice_id: string | null
          provider_payment_id: string | null
          raw: Json | null
          status: string
          updated_at: string
          user_id: string
        }
        Insert: {
          amount: number
          checkout_url?: string | null
          created_at?: string
          credits?: number
          currency?: string
          fulfilled_at?: string | null
          id?: string
          kind: string
          pack_id?: string | null
          plan_id?: string | null
          provider?: string
          provider_invoice_id?: string | null
          provider_payment_id?: string | null
          raw?: Json | null
          status?: string
          updated_at?: string
          user_id: string
        }
        Update: {
          amount?: number
          checkout_url?: string | null
          created_at?: string
          credits?: number
          currency?: string
          fulfilled_at?: string | null
          id?: string
          kind?: string
          pack_id?: string | null
          plan_id?: string | null
          provider?: string
          provider_invoice_id?: string | null
          provider_payment_id?: string | null
          raw?: Json | null
          status?: string
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "payments_pack_id_fkey"
            columns: ["pack_id"]
            isOneToOne: false
            referencedRelation: "credit_packs"
            referencedColumns: ["id"]
          },
          {
            foreignKeyName: "payments_plan_id_fkey"
            columns: ["plan_id"]
            isOneToOne: false
            referencedRelation: "plans"
            referencedColumns: ["id"]
          },
        ]
      }
      plans: {
        Row: {
          created_at: string
          credits_grant: number
          currency: string
          description: string | null
          id: string
          is_active: boolean
          live_seconds_limit: number
          llm_limit: number
          name: string
          name_ar: string | null
          period_days: number
          price_cents: number
          reset_period: string
          sort_order: number
          stripe_price_id: string | null
          stt_limit: number
          tts_limit: number
        }
        Insert: {
          created_at?: string
          credits_grant?: number
          currency?: string
          description?: string | null
          id: string
          is_active?: boolean
          live_seconds_limit?: number
          llm_limit?: number
          name: string
          name_ar?: string | null
          period_days?: number
          price_cents?: number
          reset_period?: string
          sort_order?: number
          stripe_price_id?: string | null
          stt_limit?: number
          tts_limit?: number
        }
        Update: {
          created_at?: string
          credits_grant?: number
          currency?: string
          description?: string | null
          id?: string
          is_active?: boolean
          live_seconds_limit?: number
          llm_limit?: number
          name?: string
          name_ar?: string | null
          period_days?: number
          price_cents?: number
          reset_period?: string
          sort_order?: number
          stripe_price_id?: string | null
          stt_limit?: number
          tts_limit?: number
        }
        Relationships: []
      }
      profiles: {
        Row: {
          avatar_url: string | null
          company: string | null
          created_at: string
          full_name: string | null
          id: string
          phone: string | null
        }
        Insert: {
          avatar_url?: string | null
          company?: string | null
          created_at?: string
          full_name?: string | null
          id: string
          phone?: string | null
        }
        Update: {
          avatar_url?: string | null
          company?: string | null
          created_at?: string
          full_name?: string | null
          id?: string
          phone?: string | null
        }
        Relationships: []
      }
      stt_results: {
        Row: {
          audio_bucket: string | null
          audio_bytes: number | null
          audio_mime: string | null
          audio_path: string | null
          audio_url: string | null
          coda_error: string | null
          coda_model: string | null
          coda_ms: number | null
          coda_transcript: string | null
          created_at: string
          duration_seconds: number | null
          filename: string | null
          google_billed_seconds: number | null
          google_confidence: number | null
          google_error: string | null
          google_language_code: string | null
          google_model: string | null
          google_ms: number | null
          google_transcript: string | null
          id: string
          lang: string | null
          metadata: Json
          preferred: string | null
          preferred_at: string | null
          updated_at: string
          user_id: string
        }
        Insert: {
          audio_bucket?: string | null
          audio_bytes?: number | null
          audio_mime?: string | null
          audio_path?: string | null
          audio_url?: string | null
          coda_error?: string | null
          coda_model?: string | null
          coda_ms?: number | null
          coda_transcript?: string | null
          created_at?: string
          duration_seconds?: number | null
          filename?: string | null
          google_billed_seconds?: number | null
          google_confidence?: number | null
          google_error?: string | null
          google_language_code?: string | null
          google_model?: string | null
          google_ms?: number | null
          google_transcript?: string | null
          id?: string
          lang?: string | null
          metadata?: Json
          preferred?: string | null
          preferred_at?: string | null
          updated_at?: string
          user_id: string
        }
        Update: {
          audio_bucket?: string | null
          audio_bytes?: number | null
          audio_mime?: string | null
          audio_path?: string | null
          audio_url?: string | null
          coda_error?: string | null
          coda_model?: string | null
          coda_ms?: number | null
          coda_transcript?: string | null
          created_at?: string
          duration_seconds?: number | null
          filename?: string | null
          google_billed_seconds?: number | null
          google_confidence?: number | null
          google_error?: string | null
          google_language_code?: string | null
          google_model?: string | null
          google_ms?: number | null
          google_transcript?: string | null
          id?: string
          lang?: string | null
          metadata?: Json
          preferred?: string | null
          preferred_at?: string | null
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      subscriptions: {
        Row: {
          created_at: string
          current_period_end: string | null
          is_pro: boolean
          plan_id: string
          status: string
          stripe_customer_id: string | null
          stripe_subscription_id: string | null
          updated_at: string
          user_id: string
        }
        Insert: {
          created_at?: string
          current_period_end?: string | null
          is_pro?: boolean
          plan_id?: string
          status?: string
          stripe_customer_id?: string | null
          stripe_subscription_id?: string | null
          updated_at?: string
          user_id: string
        }
        Update: {
          created_at?: string
          current_period_end?: string | null
          is_pro?: boolean
          plan_id?: string
          status?: string
          stripe_customer_id?: string | null
          stripe_subscription_id?: string | null
          updated_at?: string
          user_id?: string
        }
        Relationships: [
          {
            foreignKeyName: "subscriptions_plan_id_fkey"
            columns: ["plan_id"]
            isOneToOne: false
            referencedRelation: "plans"
            referencedColumns: ["id"]
          },
        ]
      }
      usage_counters: {
        Row: {
          live_seconds_used: number
          llm_used: number
          period_start: string
          stt_used: number
          tts_used: number
          updated_at: string
          user_id: string
        }
        Insert: {
          live_seconds_used?: number
          llm_used?: number
          period_start: string
          stt_used?: number
          tts_used?: number
          updated_at?: string
          user_id: string
        }
        Update: {
          live_seconds_used?: number
          llm_used?: number
          period_start?: string
          stt_used?: number
          tts_used?: number
          updated_at?: string
          user_id?: string
        }
        Relationships: []
      }
      usage_events: {
        Row: {
          amount: number
          api_key_id: string | null
          created_at: string
          id: number
          kind: string
          metadata: Json | null
          user_id: string
        }
        Insert: {
          amount?: number
          api_key_id?: string | null
          created_at?: string
          id?: never
          kind: string
          metadata?: Json | null
          user_id: string
        }
        Update: {
          amount?: number
          api_key_id?: string | null
          created_at?: string
          id?: never
          kind?: string
          metadata?: Json | null
          user_id?: string
        }
        Relationships: []
      }
    }
    Views: {
      [_ in never]: never
    }
    Functions: {
      activate_plan: {
        Args: {
          p_days?: number
          p_plan: string
          p_ref?: string
          p_user: string
        }
        Returns: Json
      }
      admin_bootstrap_owner: { Args: { p_auth_user: string }; Returns: Json }
      admin_check_login_rate_limit: {
        Args: { p_email: string; p_ip: unknown }
        Returns: Json
      }
      admin_create_session: {
        Args: {
          p_auth_user: string
          p_csrf_hash: string
          p_ip: unknown
          p_token_hash: string
          p_user_agent: string
        }
        Returns: Json
      }
      admin_get_user: {
        Args: { p_token_hash: string; p_user_id: string }
        Returns: Json
      }
      admin_invalidate_credentials: {
        Args: { p_auth_user: string }
        Returns: boolean
      }
      admin_list_audit: {
        Args: {
          p_action?: string
          p_actor?: string
          p_page?: number
          p_page_size?: number
          p_search?: string
          p_token_hash: string
        }
        Returns: Json
      }
      admin_list_bookings: {
        Args: {
          p_direction?: string
          p_page?: number
          p_page_size?: number
          p_search?: string
          p_sort?: string
          p_source?: string
          p_status?: string
          p_token_hash: string
        }
        Returns: Json
      }
      admin_list_plans: { Args: { p_token_hash: string }; Returns: Json }
      admin_list_users: {
        Args: {
          p_direction?: string
          p_page?: number
          p_page_size?: number
          p_plan?: string
          p_search?: string
          p_sort?: string
          p_status?: string
          p_token_hash: string
        }
        Returns: Json
      }
      admin_overview: {
        Args: { p_range?: string; p_token_hash: string }
        Returns: Json
      }
      admin_record_login_attempt: {
        Args: { p_email: string; p_ip: unknown; p_succeeded: boolean }
        Returns: Json
      }
      admin_revoke_session: {
        Args: { p_reason?: string; p_token_hash: string }
        Returns: boolean
      }
      admin_update_booking_status: {
        Args: {
          p_booking_id: string
          p_csrf_hash: string
          p_ip: unknown
          p_request_id: string
          p_status: string
          p_token_hash: string
        }
        Returns: Json
      }
      admin_update_plan_limits: {
        Args: {
          p_agent_minutes: number
          p_csrf_hash: string
          p_ip: unknown
          p_llm_requests: number
          p_plan_id: string
          p_request_id: string
          p_stt_requests: number
          p_token_hash: string
          p_tts_requests: number
        }
        Returns: Json
      }
      admin_update_user_limits: {
        Args: {
          p_agent_minutes?: number
          p_csrf_hash: string
          p_ip?: unknown
          p_llm_requests?: number
          p_request_id?: string
          p_stt_requests?: number
          p_token_hash: string
          p_tts_requests?: number
          p_user_id: string
        }
        Returns: Json
      }
      admin_validate_session: {
        Args: { p_token_hash: string; p_touch?: boolean }
        Returns: Json
      }
      consume_quota: {
        Args: { p_amount: number; p_kind: string; p_user: string }
        Returns: Json
      }
      credit_balance: { Args: { p_user: string }; Returns: number }
      get_quota_status: { Args: never; Returns: Json }
      grant_credits: {
        Args: {
          p_amount: number
          p_metadata?: Json
          p_reason: string
          p_ref?: string
          p_user: string
        }
        Returns: Json
      }
      live_remaining: { Args: { p_user: string }; Returns: number }
      period_start_for: { Args: { p_reset: string }; Returns: string }
      plan_for_user: {
        Args: { p_user: string }
        Returns: {
          created_at: string
          credits_grant: number
          currency: string
          description: string | null
          id: string
          is_active: boolean
          live_seconds_limit: number
          llm_limit: number
          name: string
          name_ar: string | null
          period_days: number
          price_cents: number
          reset_period: string
          sort_order: number
          stripe_price_id: string | null
          stt_limit: number
          tts_limit: number
        }
        SetofOptions: {
          from: "*"
          to: "plans"
          isOneToOne: true
          isSetofReturn: false
        }
      }
      refund_credits: {
        Args: {
          p_amount: number
          p_api_key?: string
          p_metadata?: Json
          p_reason?: string
          p_user: string
        }
        Returns: Json
      }
      refund_quota: {
        Args: { p_amount: number; p_kind: string; p_user: string }
        Returns: undefined
      }
      resolve_api_key: { Args: { p_hash: string }; Returns: string }
      resolve_api_key_full: { Args: { p_hash: string }; Returns: Json }
      spend_credits: {
        Args: {
          p_amount: number
          p_api_key?: string
          p_metadata?: Json
          p_reason?: string
          p_user: string
        }
        Returns: Json
      }
    }
    Enums: {
      [_ in never]: never
    }
    CompositeTypes: {
      [_ in never]: never
    }
  }
}

type DatabaseWithoutInternals = Omit<Database, "__InternalSupabase">

type DefaultSchema = DatabaseWithoutInternals[Extract<keyof Database, "public">]

export type Tables<
  DefaultSchemaTableNameOrOptions extends
    | keyof (DefaultSchema["Tables"] & DefaultSchema["Views"])
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
        DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? (DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"] &
      DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Views"])[TableName] extends {
      Row: infer R
    }
    ? R
    : never
  : DefaultSchemaTableNameOrOptions extends keyof (DefaultSchema["Tables"] &
        DefaultSchema["Views"])
    ? (DefaultSchema["Tables"] &
        DefaultSchema["Views"])[DefaultSchemaTableNameOrOptions] extends {
        Row: infer R
      }
      ? R
      : never
    : never

export type TablesInsert<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Insert: infer I
    }
    ? I
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Insert: infer I
      }
      ? I
      : never
    : never

export type TablesUpdate<
  DefaultSchemaTableNameOrOptions extends
    | keyof DefaultSchema["Tables"]
    | { schema: keyof DatabaseWithoutInternals },
  TableName extends DefaultSchemaTableNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"]
    : never = never,
> = DefaultSchemaTableNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaTableNameOrOptions["schema"]]["Tables"][TableName] extends {
      Update: infer U
    }
    ? U
    : never
  : DefaultSchemaTableNameOrOptions extends keyof DefaultSchema["Tables"]
    ? DefaultSchema["Tables"][DefaultSchemaTableNameOrOptions] extends {
        Update: infer U
      }
      ? U
      : never
    : never

export type Enums<
  DefaultSchemaEnumNameOrOptions extends
    | keyof DefaultSchema["Enums"]
    | { schema: keyof DatabaseWithoutInternals },
  EnumName extends DefaultSchemaEnumNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"]
    : never = never,
> = DefaultSchemaEnumNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[DefaultSchemaEnumNameOrOptions["schema"]]["Enums"][EnumName]
  : DefaultSchemaEnumNameOrOptions extends keyof DefaultSchema["Enums"]
    ? DefaultSchema["Enums"][DefaultSchemaEnumNameOrOptions]
    : never

export type CompositeTypes<
  PublicCompositeTypeNameOrOptions extends
    | keyof DefaultSchema["CompositeTypes"]
    | { schema: keyof DatabaseWithoutInternals },
  CompositeTypeName extends PublicCompositeTypeNameOrOptions extends {
    schema: keyof DatabaseWithoutInternals
  }
    ? keyof DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"]
    : never = never,
> = PublicCompositeTypeNameOrOptions extends {
  schema: keyof DatabaseWithoutInternals
}
  ? DatabaseWithoutInternals[PublicCompositeTypeNameOrOptions["schema"]]["CompositeTypes"][CompositeTypeName]
  : PublicCompositeTypeNameOrOptions extends keyof DefaultSchema["CompositeTypes"]
    ? DefaultSchema["CompositeTypes"][PublicCompositeTypeNameOrOptions]
    : never

export const Constants = {
  public: {
    Enums: {},
  },
} as const

