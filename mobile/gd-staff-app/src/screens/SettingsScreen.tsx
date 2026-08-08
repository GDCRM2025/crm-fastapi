import React from "react";
import { Pressable, SafeAreaView, Text, View } from "react-native";

import { useAuth } from "../auth/AuthContext";
import { GD } from "../theme";

export function SettingsScreen() {
  const { me, role, logout } = useAuth();

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 }}>
        <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }}>Cuenta</Text>
        <Text style={{ color: GD.muted }}>{me?.name || me?.username || "Usuario"}</Text>
        <Text style={{ color: GD.muted, marginTop: 4 }}>Rol: {role || "—"}</Text>
      </View>

      <View style={{ padding: 12 }}>
        <Pressable
          onPress={() => void logout()}
          style={({ pressed }) => ({
            backgroundColor: "#FEF2F2",
            borderColor: "rgba(239,68,68,.25)",
            borderWidth: 1,
            borderRadius: 16,
            paddingVertical: 12,
            paddingHorizontal: 14,
            opacity: pressed ? 0.9 : 1,
            alignItems: "center",
          })}
        >
          <Text style={{ color: GD.danger, fontWeight: "900" }}>Cerrar sesión</Text>
        </Pressable>
      </View>
    </SafeAreaView>
  );
}
