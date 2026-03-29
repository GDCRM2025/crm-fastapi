export type ChatThread = {
  id_thread: number;
  kind: string;
  title: string | null;
  updated_at?: string | null;
  last_message?: string | null;
  last_at?: string | null;
  unread?: number | null;
  members?: { user_id: number; name?: string | null; email?: string | null }[];
};

export type ChatMessage = {
  id_message: number;
  id_thread: number;
  sender_id: number | null;
  sender_name: string | null;
  sender_email: string | null;
  message: string;
  created_at: string;
};

