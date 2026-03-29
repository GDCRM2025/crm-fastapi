import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, RefreshControl, SafeAreaView, Text, View } from "react-native";

import { apiAuthedJSON } from "../../api/client";
import { GD } from "../../theme";

type LiveItem = {
  id_usuario: number | null;
  username: string | null;
  nombre: string | null;
  role: string | null;
  lat: number | null;
  lng: number | null;
  accuracy: number | null;
  speed: number | null;
  heading: number | null;
  battery: number | null;
  created_at: string;
};

export function GPSLiveScreen() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [items, setItems] = useState<LiveItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      const j = await apiAuthedJSON<any>("/gps/live");
      setItems((j?.items || []) as LiveItem[]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const t = setInterval(load, 10_000);
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

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 }}>
        <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }}>GPS Live</Text>
        <Text style={{ color: GD.muted }}>Últimas ubicaciones (últimas 2 horas)</Text>
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator />
        </View>
      ) : (
        <FlatList
          data={items}
          keyExtractor={(it, idx) => String(it.id_usuario || idx)}
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
            <View style={{ backgroundColor: GD.surface, borderRadius: 16, borderWidth: 1, borderColor: GD.border, padding: 14, marginBottom: 10 }}>
              <Text style={{ fontWeight: "900", color: GD.text }} numberOfLines={1}>
                {item.nombre || item.username || "Usuario"} · {item.role || "—"}
              </Text>
              <Text style={{ color: GD.muted, marginTop: 6 }}>
                {item.lat?.toFixed?.(6) ?? "—"}, {item.lng?.toFixed?.(6) ?? "—"} · acc {Math.round(item.accuracy || 0)}m · bat {item.battery ?? "—"}%
              </Text>
              <Text style={{ color: GD.muted, marginTop: 6 }} numberOfLines={1}>
                {String(item.created_at || "").replace("T", " ").slice(0, 19)}
              </Text>
            </View>
          )}
        />
      )}
    </SafeAreaView>
  );
}

