import React, { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, RefreshControl, SafeAreaView, Text, View } from "react-native";

import { apiAuthedJSON } from "../../api/client";
import { GD } from "../../theme";
import { CalendarEvent, LeadInfo } from "./types";

function ymd(d: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

function weekdayLabel(d: Date): string {
  return d.toLocaleDateString("es-CL", { weekday: "long", day: "2-digit", month: "2-digit" });
}

export function CalendarScreen() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [items, setItems] = useState<CalendarEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<LeadInfo | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);

  const range = useMemo(() => {
    const now = new Date();
    const from = new Date(now);
    from.setDate(from.getDate() - 1);
    const to = new Date(now);
    to.setDate(to.getDate() + 7);
    return { from, to, fromYmd: ymd(from), toYmd: ymd(to) };
  }, []);

  const load = useCallback(async () => {
    setError(null);
    try {
      const j = await apiAuthedJSON<any>(`/calendar/events?from_date=${range.fromYmd}&to_date=${range.toYmd}&limit=250`);
      setItems((j?.items || []) as CalendarEvent[]);
    } catch (e: any) {
      setError(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }, [range.fromYmd, range.toYmd]);

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

  async function openLead(idLead: number) {
    setDetailError(null);
    try {
      const j = await apiAuthedJSON<any>(`/calendar/lead_info?id_lead=${idLead}`);
      setSelected((j?.lead || null) as LeadInfo | null);
    } catch (e: any) {
      setDetailError(String(e?.message || e));
      setSelected(null);
    }
  }

  const grouped = useMemo(() => {
    const map = new Map<string, CalendarEvent[]>();
    for (const it of items) {
      const key = String(it.start || "").slice(0, 10) || "—";
      if (!map.has(key)) map.set(key, []);
      map.get(key)!.push(it);
    }
    const keys = Array.from(map.keys()).sort();
    return keys.map((k) => ({ day: k, items: map.get(k)! }));
  }, [items]);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 }}>
        <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }}>Calendario</Text>
        <Text style={{ color: GD.muted }}>
          Eventos {range.fromYmd} → {range.toYmd}
        </Text>
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator />
        </View>
      ) : (
        <FlatList
          data={grouped}
          keyExtractor={(g) => g.day}
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
          renderItem={({ item: g }) => {
            const d = new Date(g.day + "T12:00:00");
            return (
              <View style={{ marginBottom: 12 }}>
                <Text style={{ marginBottom: 8, fontWeight: "900", color: GD.text }}>{weekdayLabel(d)}</Text>
                {g.items.map((ev) => (
                  <Pressable
                    key={String(ev.id_lead || ev.id_evento || Math.random())}
                    onPress={() => (ev.id_lead ? openLead(Number(ev.id_lead)) : undefined)}
                    style={({ pressed }) => ({
                      backgroundColor: GD.surface,
                      borderRadius: 16,
                      borderWidth: 1,
                      borderColor: GD.border,
                      padding: 12,
                      marginBottom: 8,
                      opacity: pressed ? 0.92 : 1,
                    })}
                  >
                    <Text style={{ fontWeight: "900", color: GD.text }} numberOfLines={1}>
                      {ev.title || "Evento"}
                    </Text>
                    <Text style={{ color: GD.muted, marginTop: 4 }} numberOfLines={2}>
                      {ev.marca || "—"} · {ev.comuna || "—"} · OPS: {ev.ops || 0}
                    </Text>
                    <Text style={{ color: GD.muted, marginTop: 4 }} numberOfLines={1}>
                      {String(ev.start || "").slice(11, 16)} → {String(ev.end || "").slice(11, 16)} · {ev.location || "Sin dirección"}
                    </Text>
                  </Pressable>
                ))}
              </View>
            );
          }}
        />
      )}

      {detailError ? (
        <View style={{ position: "absolute", left: 12, right: 12, bottom: 12, padding: 12, borderRadius: 16, backgroundColor: "#FEF2F2", borderWidth: 1, borderColor: "rgba(239,68,68,.25)" }}>
          <Text style={{ color: GD.danger, fontWeight: "900" }}>No pude abrir detalle</Text>
          <Text style={{ color: GD.danger, marginTop: 4 }}>{detailError}</Text>
        </View>
      ) : null}

      {selected ? (
        <View style={{ position: "absolute", left: 12, right: 12, bottom: 12, padding: 14, borderRadius: 16, backgroundColor: GD.surface, borderWidth: 1, borderColor: GD.border }}>
          <Text style={{ fontWeight: "900", color: GD.text }} numberOfLines={1}>
            #{selected.id_lead} · {selected.cliente || "Cliente"}
          </Text>
          <Text style={{ color: GD.muted, marginTop: 6 }} numberOfLines={2}>
            {selected.marca || "—"} · {selected.comuna || "—"} · {selected.horario || "Horario por confirmar"}
          </Text>
          <Text style={{ color: GD.muted, marginTop: 6 }} numberOfLines={2}>
            Dir: {selected.direccion || "—"} · Tel: {selected.telefono || "—"}
          </Text>
          <Pressable
            onPress={() => setSelected(null)}
            style={({ pressed }) => ({ marginTop: 10, alignSelf: "flex-end", paddingHorizontal: 12, paddingVertical: 8, borderRadius: 12, backgroundColor: "rgba(15,23,42,.06)", opacity: pressed ? 0.9 : 1 })}
          >
            <Text style={{ fontWeight: "900", color: GD.text }}>Cerrar</Text>
          </Pressable>
        </View>
      ) : null}
    </SafeAreaView>
  );
}
