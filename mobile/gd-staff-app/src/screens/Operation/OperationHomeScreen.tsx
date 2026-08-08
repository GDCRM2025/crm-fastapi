import React from "react";
import { Pressable, SafeAreaView, ScrollView, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { GD } from "../../theme";

type Tile = {
  key: string;
  title: string;
  subtitle: string;
  icon: keyof typeof Ionicons.glyphMap;
  disabled?: boolean;
};

const tiles: Tile[] = [
  { key: "gps", title: "Conectar GPS", subtitle: "Ubicación en tiempo real", icon: "navigate" },
  { key: "ruta", title: "Ver ruta", subtitle: "GPS Live", icon: "map" },
  { key: "cal", title: "Calendario", subtitle: "Eventos asignados", icon: "calendar" },
  { key: "menu_cam", title: "Menú Camaleón", subtitle: "Recetas + ingredientes", icon: "restaurant" },
  { key: "menu_gou", title: "Menú Gourmet", subtitle: "Recetas + ingredientes", icon: "restaurant" },
  { key: "menu_exp", title: "Menú Express", subtitle: "Recetas + ingredientes", icon: "restaurant" },
  { key: "menu_sab", title: "Menú Del Sabor", subtitle: "Recetas + ingredientes", icon: "restaurant" },
  { key: "flow", title: "Mas Flow", subtitle: "En construcción", icon: "construct", disabled: true },
  { key: "petras", title: "Petras", subtitle: "En construcción", icon: "construct", disabled: true },
  { key: "uni", title: "Universidad GD", subtitle: "Capacitación", icon: "school" },
];

export function OperationHomeScreen({ navigation }: any) {
  function onTile(key: string) {
    if (key === "gps") navigation.navigate("GPSPing");
    else if (key === "ruta") navigation.navigate("GPSLive");
    else if (key === "cal") navigation.navigate("Calendar");
    else if (key === "menu_cam") navigation.navigate("RecipesList", { marca: "CAMALEON" });
    else if (key === "menu_gou") navigation.navigate("RecipesList", { marca: "GOURMET" });
    else if (key === "menu_exp") navigation.navigate("RecipesList", { marca: "EXPRESS" });
    else if (key === "menu_sab") navigation.navigate("RecipesList", { marca: "DEL SABOR" });
    else if (key === "uni") navigation.navigate("University");
  }
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 }}>
        <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }}>Operación</Text>
        <Text style={{ color: GD.muted }}>Operadores / Conductores / Operaciones</Text>
      </View>

      <ScrollView contentContainerStyle={{ padding: 12, paddingBottom: 24 }}>
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 10 }}>
          {tiles.map((t) => (
            <Pressable
              key={t.key}
              onPress={() => (!t.disabled ? onTile(t.key) : undefined)}
              style={{
                width: "48%",
                backgroundColor: GD.surface,
                borderRadius: 16,
                borderWidth: 1,
                borderColor: GD.border,
                padding: 12,
                opacity: t.disabled ? 0.6 : 1,
              }}
            >
              <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                <View
                  style={{
                    width: 38,
                    height: 38,
                    borderRadius: 12,
                    backgroundColor: "rgba(25,195,125,.12)",
                    alignItems: "center",
                    justifyContent: "center",
                  }}
                >
                  <Ionicons name={t.icon} size={20} color={GD.brandDark} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ fontWeight: "900", color: GD.text }} numberOfLines={1}>
                    {t.title}
                  </Text>
                  <Text style={{ color: GD.muted, marginTop: 2 }} numberOfLines={1}>
                    {t.subtitle}
                  </Text>
                </View>
              </View>
            </Pressable>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
