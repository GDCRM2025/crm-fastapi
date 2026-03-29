import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, RefreshControl, SafeAreaView, Text, View } from "react-native";

import { apiAuthedJSON } from "../api/client";
import { GD } from "../theme";

type Notif = {
  id: number;
  created_at: string;
  kind: string;
  id_lead: number | null;
  title: string;
  body: string;
  payload: any;
  read_at: string | null;
};

export function NotificationsScreen() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [items, setItems] = useState<Notif[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const j = await apiAuthedJSON<any>("/notifications/system?limit=80&unread_only=0");
      setItems((j?.items || []) as Notif[]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, [load]);

  async function onRefresh() {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }

  async function markRead(id: number) {
    try {
      await apiAuthedJSON<any>(`/notifications/system/${id}/read`, { method: "POST" });
      await load();
    } catch (e: any) {
      setError(String(e?.message || e));
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 }}>
        <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }}>Notificaciones</Text>
        <Text style={{ color: GD.muted }}>Eventos agendados/modificados y alertas internas</Text>
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator />
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(n) => String(n.id)}
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
          renderItem={({ item }) => {
            const unread = !item.read_at;
            return (
              <Pressable
                onPress={() => (unread ? markRead(item.id) : undefined)}
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
                <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 10 }}>
                  <Text style={{ fontWeight: "900", color: GD.text, flex: 1 }} numberOfLines={1}>
                    {item.title || item.kind}
                  </Text>
                  {unread ? (
                    <View style={{ backgroundColor: "rgba(25,195,125,.15)", borderRadius: 99, paddingHorizontal: 10, paddingVertical: 4 }}>
                      <Text style={{ color: GD.brandDark, fontWeight: "900" }}>Nuevo</Text>
                    </View>
                  ) : null}
                </View>
                <Text style={{ color: GD.muted, marginTop: 6, lineHeight: 20 }}>{item.body || "—"}</Text>
                {unread ? <Text style={{ color: GD.muted, marginTop: 8 }}>Toca para marcar como leído</Text> : null}
              </Pressable>
            );
          }}
        />
      )}
    </SafeAreaView>
  );
}

