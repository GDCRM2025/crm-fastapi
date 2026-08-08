import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, RefreshControl, SafeAreaView, Text, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";

import { apiAuthedJSON } from "../../api/client";
import { GD } from "../../theme";
import { ChatThread } from "./types";
import { Ionicons } from "@expo/vector-icons";

type RootStackParamList = {
  Threads: undefined;
  Users: undefined;
  Thread: { id_thread: number; title?: string };
};

type Props = NativeStackScreenProps<RootStackParamList, "Threads">;

export function ThreadsScreen({ navigation }: Props) {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [items, setItems] = useState<ChatThread[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const j = await apiAuthedJSON<any>("/chat/threads?limit=60");
      setItems((j?.items || []) as ChatThread[]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function onRefresh() {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8, flexDirection: "row", justifyContent: "space-between", alignItems: "center" }}>
        <View>
          <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }}>Chat</Text>
          <Text style={{ color: GD.muted }}>Conversaciones internas</Text>
        </View>
        <Pressable
          onPress={() => navigation.navigate("Users")}
          style={({ pressed }) => ({
            width: 44,
            height: 44,
            borderRadius: 14,
            backgroundColor: "rgba(25,195,125,.12)",
            alignItems: "center",
            justifyContent: "center",
            opacity: pressed ? 0.9 : 1,
            borderWidth: 1,
            borderColor: "rgba(25,195,125,.18)",
          })}
        >
          <Ionicons name="create-outline" size={22} color={GD.brandDark} />
        </Pressable>
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator />
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(t) => String(t.id_thread)}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} />}
          contentContainerStyle={{ padding: 12, paddingBottom: 24 }}
          ListEmptyComponent={
            <View style={{ padding: 16, backgroundColor: GD.surface, borderRadius: 16, borderWidth: 1, borderColor: GD.border }}>
              <Text style={{ fontWeight: "700", color: GD.text }}>Sin conversaciones</Text>
              <Text style={{ color: GD.muted, marginTop: 6 }}>
                Te falta iniciar una conversación desde el CRM (por ahora). Luego lo dejamos dentro de la app.
              </Text>
            </View>
          }
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
              onPress={() => navigation.navigate("Thread", { id_thread: item.id_thread, title: item.title || "Chat" })}
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
              <Text style={{ fontWeight: "800", color: GD.text }} numberOfLines={1}>
                {item.title || "Chat"}
              </Text>
              <Text style={{ color: GD.muted, marginTop: 4 }} numberOfLines={1}>
                {item.last_message || "—"}
              </Text>
            </Pressable>
          )}
        />
      )}
    </SafeAreaView>
  );
}
