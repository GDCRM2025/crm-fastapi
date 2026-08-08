import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, RefreshControl, SafeAreaView, Text, TextInput, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";

import { apiAuthedJSON } from "../../api/client";
import { GD } from "../../theme";

type RootStackParamList = {
  Threads: undefined;
  Users: undefined;
  Thread: { id_thread: number; title?: string };
};

type Props = NativeStackScreenProps<RootStackParamList, "Users">;

type ChatUser = { id: number; name: string; email: string; role: string; avatar_url?: string | null };

export function UsersScreen({ navigation }: Props) {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<ChatUser[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const j = await apiAuthedJSON<any>(`/chat/users?include_staff=1&limit=200&q=${encodeURIComponent(q.trim())}`);
      setItems((j?.items || []) as ChatUser[]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, [q]);

  useEffect(() => {
    const t = setTimeout(load, 220);
    return () => clearTimeout(t);
  }, [load]);

  async function onRefresh() {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  async function startChat(userId: number, title: string) {
    try {
      const j = await apiAuthedJSON<any>("/chat/threads/direct", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId }),
      });
      const idThread = Number(j?.id_thread || 0);
      if (!idThread) throw new Error("No se pudo crear el chat");
      navigation.replace("Thread", { id_thread: idThread, title });
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 }}>
        <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }}>Nuevo chat</Text>
        <Text style={{ color: GD.muted }}>Busca a una persona y crea conversación</Text>
      </View>

      <View style={{ paddingHorizontal: 12, paddingBottom: 6 }}>
        <TextInput
          value={q}
          onChangeText={setQ}
          placeholder="Buscar (nombre o email)…"
          placeholderTextColor="rgba(15,23,42,.35)"
          style={{
            backgroundColor: GD.surface,
            borderRadius: 14,
            borderWidth: 1,
            borderColor: GD.border,
            paddingVertical: 10,
            paddingHorizontal: 12,
            color: GD.text,
          }}
        />
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator />
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(u) => String(u.id)}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          contentContainerStyle={{ padding: 12, paddingBottom: 24 }}
          ListHeaderComponent={
            error ? (
              <View style={{ marginBottom: 10, padding: 12, borderRadius: 14, backgroundColor: "#FEF2F2", borderWidth: 1, borderColor: "rgba(239,68,68,.25)" }}>
                <Text style={{ color: GD.danger, fontWeight: "700" }}>Error</Text>
                <Text style={{ color: GD.danger, marginTop: 4 }}>{error}</Text>
              </View>
            ) : null
          }
          renderItem={({ item }) => (
            <Pressable
              onPress={() => startChat(item.id, item.name || item.email || "Chat")}
              style={({ pressed }) => ({
                backgroundColor: GD.surface,
                borderRadius: 16,
                borderWidth: 1,
                borderColor: GD.border,
                padding: 14,
                marginBottom: 10,
                opacity: pressed ? 0.92 : 1,
              })}
            >
              <Text style={{ fontWeight: "900", color: GD.text }} numberOfLines={1}>
                {item.name || item.email}
              </Text>
              <Text style={{ color: GD.muted, marginTop: 4 }} numberOfLines={1}>
                {item.email || "—"} · {item.role || "—"}
              </Text>
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}
