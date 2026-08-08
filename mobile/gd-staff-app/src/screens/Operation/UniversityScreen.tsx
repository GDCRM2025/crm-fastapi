import React from "react";
import { Linking, Pressable, SafeAreaView, ScrollView, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";

import { GD } from "../../theme";

type LinkRow = {
  title: string;
  subtitle: string;
  url: string;
  icon: keyof typeof Ionicons.glyphMap;
};

const links: LinkRow[] = [
  {
    title: "Universidad GD",
    subtitle: "Material + capacitación",
    url: "https://greendiamond.cl/crm",
    icon: "school",
  },
  {
    title: "Planilla disponibilidad (ops)",
    subtitle: "Inscripción / turnos",
    url: "https://docs.google.com/spreadsheets/d/1MZBTh20Sm3BNeF2z4vTalLQY2-Me8AmazaU4P-P3CQc/edit",
    icon: "calendar",
  },
];

export function UniversityScreen({ navigation }: any) {
  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 10 }}>
        <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
          <Pressable onPress={() => navigation.goBack()} style={{ paddingVertical: 6, paddingRight: 12 }}>
            <Ionicons name="chevron-back" size={22} color={GD.text} />
          </Pressable>
          <Text style={{ flex: 1, fontSize: 18, fontWeight: "900", color: GD.text }} numberOfLines={1}>
            Universidad GD
          </Text>
          <View style={{ width: 38 }} />
        </View>
        <Text style={{ color: GD.muted, marginTop: 6 }}>Accesos rápidos (por ahora). Luego lo hacemos 100% nativo.</Text>
      </View>

      <ScrollView contentContainerStyle={{ padding: 12, paddingBottom: 24 }}>
        <View style={{ gap: 10 }}>
          {links.map((l) => (
            <Pressable
              key={l.url}
              onPress={() => Linking.openURL(l.url)}
              style={{
                backgroundColor: GD.surface,
                borderRadius: 16,
                borderWidth: 1,
                borderColor: GD.border,
                padding: 12,
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
                  <Ionicons name={l.icon} size={20} color={GD.brandDark} />
                </View>
                <View style={{ flex: 1 }}>
                  <Text style={{ color: GD.text, fontWeight: "900" }} numberOfLines={1}>
                    {l.title}
                  </Text>
                  <Text style={{ color: GD.muted, marginTop: 2 }} numberOfLines={1}>
                    {l.subtitle}
                  </Text>
                </View>
                <Ionicons name="open-outline" size={18} color={GD.muted} />
              </View>
            </Pressable>
          ))}
        </View>
      </ScrollView>
    </SafeAreaView>
  );
}
