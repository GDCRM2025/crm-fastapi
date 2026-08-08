import React, { useState } from "react";
import { ActivityIndicator, Pressable, SafeAreaView, Text, TextInput, View } from "react-native";

import { useAuth } from "../auth/AuthContext";
import { GD } from "../theme";

export function LoginScreen() {
  const { loading, login, error } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function onLogin() {
    if (busy) return;
    setBusy(true);
    try {
      await login(username, password);
    } finally {
      setBusy(false);
    }
  }

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ padding: 18, paddingTop: 24, gap: 12 }}>
        <Text style={{ fontSize: 26, fontWeight: "800", color: GD.text }}>GreenDiamond App</Text>
        <Text style={{ color: GD.muted, lineHeight: 20 }}>
          Entra con el mismo usuario del CRM. (Chat + notificaciones para todo el staff).
        </Text>

        <View style={{ backgroundColor: GD.surface, borderRadius: 16, padding: 14, borderWidth: 1, borderColor: GD.border }}>
          <Text style={{ color: GD.muted, marginBottom: 6 }}>Usuario / Email</Text>
          <TextInput
            value={username}
            onChangeText={setUsername}
            autoCapitalize="none"
            placeholder="usuario@dominio.cl"
            placeholderTextColor="rgba(15,23,42,.35)"
            style={{ paddingVertical: 10, paddingHorizontal: 12, borderRadius: 12, backgroundColor: "#F3F6FA", color: GD.text }}
          />

          <Text style={{ color: GD.muted, marginTop: 12, marginBottom: 6 }}>Contraseña</Text>
          <TextInput
            value={password}
            onChangeText={setPassword}
            secureTextEntry
            placeholder="••••••••"
            placeholderTextColor="rgba(15,23,42,.35)"
            style={{ paddingVertical: 10, paddingHorizontal: 12, borderRadius: 12, backgroundColor: "#F3F6FA", color: GD.text }}
          />

          <Pressable
            onPress={onLogin}
            style={({ pressed }) => ({
              marginTop: 14,
              backgroundColor: GD.brand,
              opacity: pressed ? 0.9 : 1,
              borderRadius: 14,
              paddingVertical: 12,
              alignItems: "center",
            })}
          >
            {busy || loading ? <ActivityIndicator color="white" /> : <Text style={{ color: "white", fontWeight: "800" }}>Entrar</Text>}
          </Pressable>

          {error ? (
            <Text style={{ color: GD.danger, marginTop: 10, lineHeight: 18 }}>
              {error}
            </Text>
          ) : null}
        </View>
      </View>
    </SafeAreaView>
  );
}
