import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, FlatList, KeyboardAvoidingView, Platform, Pressable, SafeAreaView, Text, TextInput, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";

import { apiAuthedJSON } from "../../api/client";
import { GD } from "../../theme";
import { ChatMessage } from "./types";

type RootStackParamList = {
  Threads: undefined;
  Thread: { id_thread: number; title?: string };
};

type Props = NativeStackScreenProps<RootStackParamList, "Thread">;

export function ThreadScreen({ route }: Props) {
  const idThread = route.params.id_thread;
  const [loading, setLoading] = useState(true);
  const [items, setItems] = useState<ChatMessage[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const listRef = useRef<FlatList<ChatMessage> | null>(null);

  const title = useMemo(() => route.params.title || "Chat", [route.params.title]);

  const load = useCallback(async () => {
    setError(null);
    try {
      const j = await apiAuthedJSON<any>(`/chat/threads/${idThread}/messages?limit=200`);
      setItems((j?.items || []) as ChatMessage[]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, [idThread]);

  useEffect(() => {
    load();
    const t = setInterval(load, 7000);
    return () => clearInterval(t);
  }, [load]);

  async function send() {
    const msg = text.trim();
    if (!msg || sending) return;
    setSending(true);
    setText("");
    try {
      await apiAuthedJSON<any>(`/chat/threads/${idThread}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      });
      await load();
      requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: true }));
    } catch (e: any) {
      setError(String(e?.message || e));
      setText(msg);
    } finally {
      setSending(false);
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 }}>
        <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }} numberOfLines={1}>
          {title}
        </Text>
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator />
        </View>
      ) : (
        <KeyboardAvoidingView style={{ flex: 1 }} behavior={Platform.OS === "ios" ? "padding" : undefined} keyboardVerticalOffset={84}>
          {error ? (
            <View style={{ marginHorizontal: 12, marginBottom: 8, padding: 12, borderRadius: 14, backgroundColor: "#FEF2F2", borderWidth: 1, borderColor: "rgba(239,68,68,.25)" }}>
              <Text style={{ color: GD.danger, fontWeight: "700" }}>Error</Text>
              <Text style={{ color: GD.danger, marginTop: 4 }}>{error}</Text>
            </View>
          ) : null}

          <FlatList
            ref={(r) => (listRef.current = r)}
            data={items}
            keyExtractor={(m) => String(m.id_message)}
            contentContainerStyle={{ padding: 12, paddingBottom: 12 }}
            renderItem={({ item }) => (
              <View style={{ marginBottom: 8, backgroundColor: GD.surface, borderRadius: 14, borderWidth: 1, borderColor: GD.border, padding: 12 }}>
                <Text style={{ fontWeight: "800", color: GD.text }} numberOfLines={1}>
                  {item.sender_name || item.sender_email || "Usuario"}
                </Text>
                <Text style={{ color: GD.muted, marginTop: 6, lineHeight: 20 }}>{item.message}</Text>
              </View>
            )}
            onContentSizeChange={() => requestAnimationFrame(() => listRef.current?.scrollToEnd({ animated: false }))}
          />

          <View style={{ padding: 12, paddingBottom: 16, borderTopWidth: 1, borderColor: GD.border, backgroundColor: GD.surface }}>
            <View style={{ flexDirection: "row", gap: 10 }}>
              <TextInput
                value={text}
                onChangeText={setText}
                placeholder="Escribe un mensaje…"
                placeholderTextColor="rgba(15,23,42,.35)"
                style={{
                  flex: 1,
                  paddingVertical: 10,
                  paddingHorizontal: 12,
                  borderRadius: 14,
                  backgroundColor: "#F3F6FA",
                  color: GD.text,
                }}
              />
              <Pressable
                onPress={send}
                style={({ pressed }) => ({
                  paddingHorizontal: 16,
                  borderRadius: 14,
                  alignItems: "center",
                  justifyContent: "center",
                  backgroundColor: GD.brand,
                  opacity: pressed || sending ? 0.85 : 1,
                })}
              >
                {sending ? <ActivityIndicator color="white" /> : <Text style={{ color: "white", fontWeight: "900" }}>Enviar</Text>}
              </Pressable>
            </View>
          </View>
        </KeyboardAvoidingView>
      )}
    </SafeAreaView>
  );
}

