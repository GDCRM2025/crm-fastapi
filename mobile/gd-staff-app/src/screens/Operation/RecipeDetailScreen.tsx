import React, { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Modal,
  Pressable,
  SafeAreaView,
  ScrollView,
  Text,
  TextInput,
  View,
} from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { apiAuthedJSON } from "../../api/client";
import { GD } from "../../theme";
import { useAuth } from "../../auth/AuthContext";

type Receta = {
  id_receta: number;
  producto: string;
  marca?: string | null;
  rendimiento?: number | null;
  merma_pct?: number | null;
  costos_extra?: number | null;
  unidad_base?: string | null;
  is_active?: boolean | null;
  es_sub_receta?: boolean | null;
};

type RecetaItem = {
  id_item: number;
  ingrediente: string;
  cantidad?: number | null;
  unidad?: string | null;
  costo_unitario?: number | null;
  merma_pct?: number | null;
  sub_receta_id?: number | null;
};

function canWrite(role: string): boolean {
  const r = role.toUpperCase();
  return (
    r === "ADMIN" ||
    r === "SUPERADMIN" ||
    r === "JEFE DE OPERACIONES" ||
    r === "OPERACIONES" ||
    r === "COMPRAS" ||
    r === "MICE" ||
    r === "BODEGUERO"
  );
}

export function RecipeDetailScreen({ navigation, route }: any) {
  const id_receta = Number(route.params.id_receta);
  const { role } = useAuth();
  const write = useMemo(() => canWrite(role), [role]);

  const [loading, setLoading] = useState(true);
  const [receta, setReceta] = useState<Receta | null>(null);
  const [items, setItems] = useState<RecetaItem[]>([]);
  const [err, setErr] = useState<string | null>(null);

  const [showAdd, setShowAdd] = useState(false);
  const [addIng, setAddIng] = useState("");
  const [addCant, setAddCant] = useState("");
  const [addUnidad, setAddUnidad] = useState("");
  const [addCosto, setAddCosto] = useState("");

  async function load() {
    setLoading(true);
    try {
      const j = await apiAuthedJSON<{ ok: boolean; receta: Receta; items: RecetaItem[] }>(`/ops/recetas/${id_receta}`);
      setReceta(j.receta);
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
  }, [id_receta]);

  async function addItem() {
    const ingrediente = addIng.trim();
    if (!ingrediente) return;
    const cantidad = addCant.trim() ? Number(addCant.trim().replace(",", ".")) : null;
    const costo_unitario = addCosto.trim() ? Number(addCosto.trim().replace(",", ".")) : null;
    const unidad = addUnidad.trim() || null;
    await apiAuthedJSON(`/ops/recetas/${id_receta}/items`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ingrediente, cantidad, unidad, costo_unitario }),
    });
    setAddIng("");
    setAddCant("");
    setAddUnidad("");
    setAddCosto("");
    setShowAdd(false);
    await load();
  }

  async function deleteItem(id_item: number) {
    await apiAuthedJSON(`/ops/recetas/${id_receta}/items/${id_item}`, { method: "DELETE" });
    await load();
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 10, gap: 10 }}>
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
          <Pressable onPress={() => navigation.goBack()} style={{ paddingVertical: 6, paddingRight: 12 }}>
            <Ionicons name="chevron-back" size={22} color={GD.text} />
          </Pressable>
          <Text style={{ flex: 1, fontSize: 18, fontWeight: "900", color: GD.text }} numberOfLines={1}>
            Receta
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
      </View>

      {loading ? (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center" }}>
          <ActivityIndicator />
          <Text style={{ marginTop: 10, color: GD.muted }}>Cargando…</Text>
        </View>
      ) : (
        <ScrollView contentContainerStyle={{ padding: 12, paddingBottom: 24 }}>
          {err ? (
            <View style={{ padding: 12, backgroundColor: "#fff1f2", borderRadius: 14, borderWidth: 1, borderColor: "rgba(239,68,68,.25)" }}>
              <Text style={{ color: GD.danger, fontWeight: "900" }}>Error</Text>
              <Text style={{ color: GD.text, marginTop: 4 }}>{err}</Text>
            </View>
          ) : null}

          {receta ? (
            <View style={{ backgroundColor: GD.surface, borderRadius: 16, borderWidth: 1, borderColor: GD.border, padding: 12, gap: 6 }}>
              <Text style={{ color: GD.text, fontWeight: "900", fontSize: 16 }}>{receta.producto}</Text>
              <Text style={{ color: GD.muted }}>Marca: {receta.marca || "—"}</Text>
              <Text style={{ color: GD.muted }}>Rendimiento: {receta.rendimiento ?? "—"}</Text>
            </View>
          ) : null}

          <View style={{ marginTop: 12, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
            <Text style={{ color: GD.text, fontWeight: "900", fontSize: 15 }}>Ingredientes</Text>
            {write ? (
              <Pressable
                onPress={() => setShowAdd(true)}
                style={{ paddingHorizontal: 12, paddingVertical: 8, backgroundColor: "rgba(25,195,125,.12)", borderRadius: 12 }}
              >
                <Text style={{ color: GD.brandDark, fontWeight: "900" }}>+ Agregar</Text>
              </Pressable>
            ) : null}
          </View>

          <View style={{ marginTop: 10, gap: 10 }}>
            {items.map((it) => (
              <View key={it.id_item} style={{ backgroundColor: GD.surface, borderRadius: 16, borderWidth: 1, borderColor: GD.border, padding: 12 }}>
                <View style={{ flexDirection: "row", justifyContent: "space-between", gap: 10 }}>
                  <View style={{ flex: 1 }}>
                    <Text style={{ color: GD.text, fontWeight: "900" }} numberOfLines={2}>
                      {it.ingrediente}
                    </Text>
                    <Text style={{ color: GD.muted, marginTop: 4 }}>
                      {it.cantidad ?? "—"} {it.unidad || ""} · Costo: {it.costo_unitario ?? "—"}
                    </Text>
                  </View>
                  {write ? (
                    <Pressable onPress={() => deleteItem(it.id_item)} style={{ paddingHorizontal: 10, paddingVertical: 8 }}>
                      <Ionicons name="trash" size={18} color={GD.danger} />
                    </Pressable>
                  ) : null}
                </View>
              </View>
            ))}
            {!items.length ? (
              <View style={{ padding: 16, alignItems: "center" }}>
                <Text style={{ color: GD.muted }}>Sin ingredientes aún.</Text>
              </View>
            ) : null}
          </View>
        </ScrollView>
      )}

      <Modal visible={showAdd} animationType="slide" transparent onRequestClose={() => setShowAdd(false)}>
        <View style={{ flex: 1, backgroundColor: "rgba(2,6,23,.45)", justifyContent: "flex-end" }}>
          <View style={{ backgroundColor: GD.surface, borderTopLeftRadius: 18, borderTopRightRadius: 18, padding: 14, borderWidth: 1, borderColor: GD.border }}>
            <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
              <Text style={{ color: GD.text, fontWeight: "900", fontSize: 16 }}>Agregar ingrediente</Text>
              <Pressable onPress={() => setShowAdd(false)} style={{ padding: 8 }}>
                <Ionicons name="close" size={20} color={GD.muted} />
              </Pressable>
            </View>

            <View style={{ marginTop: 12, gap: 10 }}>
              <TextInput
                value={addIng}
                onChangeText={setAddIng}
                placeholder="Ingrediente (obligatorio)"
                placeholderTextColor="rgba(15,23,42,.35)"
                style={{ borderWidth: 1, borderColor: GD.border, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10, color: GD.text, fontWeight: "700" }}
              />
              <View style={{ flexDirection: "row", gap: 10 }}>
                <TextInput
                  value={addCant}
                  onChangeText={setAddCant}
                  placeholder="Cantidad"
                  placeholderTextColor="rgba(15,23,42,.35)"
                  keyboardType="decimal-pad"
                  style={{ flex: 1, borderWidth: 1, borderColor: GD.border, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10, color: GD.text, fontWeight: "700" }}
                />
                <TextInput
                  value={addUnidad}
                  onChangeText={setAddUnidad}
                  placeholder="Unidad"
                  placeholderTextColor="rgba(15,23,42,.35)"
                  style={{ width: 110, borderWidth: 1, borderColor: GD.border, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10, color: GD.text, fontWeight: "700" }}
                />
              </View>
              <TextInput
                value={addCosto}
                onChangeText={setAddCosto}
                placeholder="Costo unitario"
                placeholderTextColor="rgba(15,23,42,.35)"
                keyboardType="decimal-pad"
                style={{ borderWidth: 1, borderColor: GD.border, borderRadius: 14, paddingHorizontal: 12, paddingVertical: 10, color: GD.text, fontWeight: "700" }}
              />

              <Pressable
                onPress={addItem}
                style={{ marginTop: 4, paddingVertical: 12, backgroundColor: GD.brand, borderRadius: 14, alignItems: "center" }}
              >
                <Text style={{ color: "white", fontWeight: "900" }}>Guardar</Text>
              </Pressable>
              <Text style={{ marginTop: 6, color: GD.muted, fontSize: 12 }}>
                Ingredientes ilimitados: puedes agregar todos los que necesites.
              </Text>
            </View>
          </View>
        </View>
      </Modal>
    </SafeAreaView>
  );
}
