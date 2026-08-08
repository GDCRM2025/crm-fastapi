import React, { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Pressable, SafeAreaView, ScrollView, Text, TextInput, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { apiAuthedJSON } from "../../api/client";
import { GD } from "../../theme";
import { MarcaKey, MARCAS } from "./brands";

type RecetaRow = {
  id_receta: number;
  producto: string;
  marca?: string | null;
  rendimiento?: number | null;
  is_active?: boolean | null;
};

export function RecipesListScreen({ navigation, route }: any) {
  const marca = route?.params?.marca || "EXPRESS";
  const marcaLabel = useMemo(() => MARCAS.find((m) => m.key === marca)?.label || marca, [marca]);

  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [items, setItems] = useState<RecetaRow[]>([]);
  const [err, setErr] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    try {
      const params: string[] = [`marca=${encodeURIComponent(marca)}`, "active_only=1"];
      if (q.trim()) params.push(`q=${encodeURIComponent(q.trim())}`);
      const j = await apiAuthedJSON<{ ok: boolean; items: RecetaRow[] }>(`/ops/recetas?${params.join("&")}`);
      setItems(Array.isArray(j.items) ? j.items : []);
      setErr(null);
    } catch (e: any) {
      setErr(String(e?.message || e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [marca]);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 10, gap: 10 }}>
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
          <Pressable onPress={() => navigation.goBack()} style={{ paddingVertical: 6, paddingRight: 12 }}>
            <Ionicons name="chevron-back" size={22} color={GD.text} />
          </Pressable>
          <Text style={{ flex: 1, fontSize: 18, fontWeight: "900", color: GD.text }} numberOfLines={1}>
            Menú {marcaLabel}
          </Text>
          <Pressable
            onPress={load}
            style={{
              paddingHorizontal: 10,
              paddingVertical: 8,
              backgroundColor: GD.surface,
              borderWidth: 1,
              borderColor: GD.border,
              borderRadius: 12,
            }}
          >
            <Ionicons name="refresh" size={18} color={GD.text} />
          </Pressable>
        </View>

        <View
          style={{
            flexDirection: "row",
            alignItems: "center",
            gap: 10,
            backgroundColor: GD.surface,
            borderWidth: 1,
            borderColor: GD.border,
            borderRadius: 14,
            paddingHorizontal: 12,
            paddingVertical: 10,
          }}
        >
          <Ionicons name="search" size={18} color={GD.muted} />
          <TextInput
            value={q}
            onChangeText={setQ}
            placeholder="Buscar receta…"
            placeholderTextColor="rgba(15,23,42,.35)"
            style={{ flex: 1, color: GD.text, fontWeight: "700" }}
            returnKeyType="search"
            onSubmitEditing={load}
          />
          <Pressable onPress={load} style={{ paddingHorizontal: 10, paddingVertical: 6, backgroundColor: "rgba(25,195,125,.12)", borderRadius: 10 }}>
            <Text style={{ color: GD.brandDark, fontWeight: "900" }}>Buscar</Text>
          </Pressable>
        </View>
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator />
          <Text style={{ marginTop: 10, color: GD.muted }}>Cargando recetas…</Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: 12, paddingBottom: 24 }}>
          {err ? (
            <View style={{ padding: 12, backgroundColor: "#fff1f2", borderRadius: 14, borderWidth: 1, borderColor: "rgba(239,68,68,.25)" }}>
              <Text style={{ color: GD.danger, fontWeight: "900" }}>Error</Text>
              <Text style={{ color: GD.text, marginTop: 4 }}>{err}</Text>
            </View>
          ) : null}

          <View style={{ marginTop: err ? 10 : 0, gap: 10 }}>
            {items.map((r) => (
              <Pressable
                key={r.id_receta}
                onPress={() => navigation.navigate("RecipeDetail", { id_receta: r.id_receta })}
                style={{
                  backgroundColor: GD.surface,
                  borderRadius: 16,
                  borderWidth: 1,
                  borderColor: GD.border,
                  padding: 12,
                }}
              >
                <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
                  <View style={{ flex: 1 }}>
                    <Text style={{ color: GD.text, fontWeight: "900", fontSize: 15 }} numberOfLines={1}>
                      {r.producto}
                    </Text>
                    <Text style={{ color: GD.muted, marginTop: 3 }} numberOfLines={1}>
                      Rendimiento: {r.rendimiento ?? "—"}
                    </Text>
                  </View>
                  <Ionicons name="chevron-forward" size={20} color={GD.muted} />
                </View>
              </Pressable>
            ))}

            {!items.length && !err ? (
              <View style={{ padding: 16, alignItems: "center" }}>
                <Text style={{ color: GD.muted }}>No hay recetas para esta marca.</Text>
              </View>
            ) : null}
          </View>
        </ScrollView>
      )}
    </SafeAreaView>
  );
}
