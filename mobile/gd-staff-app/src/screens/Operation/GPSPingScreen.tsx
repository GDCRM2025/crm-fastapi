import React, { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Pressable, SafeAreaView, Text, View } from "react-native";
import * as Location from "expo-location";
import * as Battery from "expo-battery";

import { apiAuthedJSON } from "../../api/client";
import { GD } from "../../theme";

export function GPSPingScreen() {
  const [status, setStatus] = useState<"idle" | "running">("idle");
  const [error, setError] = useState<string | null>(null);
  const [lastAt, setLastAt] = useState<string | null>(null);
  const timer = useRef<any>(null);

  const canStart = useMemo(() => status !== "running", [status]);

  async function sendOnce() {
    const { status: perm } = await Location.requestForegroundPermissionsAsync();
    if (perm !== "granted") throw new Error("Permiso de ubicación denegado");

    const pos = await Location.getCurrentPositionAsync({ accuracy: Location.Accuracy.Balanced });
    let battery: number | null = null;
    try {
      const level = await Battery.getBatteryLevelAsync();
      if (typeof level === "number") battery = Math.round(level * 100);
    } catch {
      // ignore
    }

    await apiAuthedJSON<any>("/gps/ping", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        lat: pos.coords.latitude,
        lng: pos.coords.longitude,
        accuracy: pos.coords.accuracy,
        speed: pos.coords.speed,
        heading: pos.coords.heading,
        battery,
      }),
    });
    setLastAt(new Date().toLocaleTimeString("es-CL"));
  }

  async function start() {
    if (!canStart) return;
    setError(null);
    try {
      await sendOnce();
      setStatus("running");
      timer.current = setInterval(() => {
        sendOnce().catch((e) => setError(String((e as any)?.message || e)));
      }, 30_000);
    } catch (e: any) {
      setError(String(e?.message || e));
      setStatus("idle");
    }
  }

  function stop() {
    if (timer.current) clearInterval(timer.current);
    timer.current = null;
    setStatus("idle");
  }

  useEffect(() => {
    return () => {
      if (timer.current) clearInterval(timer.current);
    };
  }, []);

  return (
    <SafeAreaView style={{ flex: 1, backgroundColor: GD.bg }}>
      <View style={{ paddingHorizontal: 16, paddingTop: 10, paddingBottom: 8 }}>
        <Text style={{ fontSize: 18, fontWeight: "800", color: GD.text }}>Conectar GPS</Text>
        <Text style={{ color: GD.muted }}>Envía tu ubicación al CRM (cada 30s mientras la pantalla esté abierta)</Text>
      </View>

      <View style={{ padding: 12, gap: 10 }}>
        <View style={{ backgroundColor: GD.surface, borderRadius: 16, borderWidth: 1, borderColor: GD.border, padding: 14 }}>
          <Text style={{ fontWeight: "900", color: GD.text }}>Estado: {status === "running" ? "Enviando" : "Detenido"}</Text>
          <Text style={{ color: GD.muted, marginTop: 6 }}>Último ping: {lastAt || "—"}</Text>
        </View>

        {error ? (
          <View style={{ padding: 12, borderRadius: 16, backgroundColor: "#FEF2F2", borderWidth: 1, borderColor: "rgba(239,68,68,.25)" }}>
            <Text style={{ color: GD.danger, fontWeight: "900" }}>Error</Text>
            <Text style={{ color: GD.danger, marginTop: 4 }}>{error}</Text>
          </View>
        ) : null}

        <Pressable
          onPress={() => (status === "running" ? stop() : void start())}
          style={({ pressed }) => ({
            backgroundColor: status === "running" ? "rgba(15,23,42,.08)" : GD.brand,
            borderRadius: 16,
            paddingVertical: 12,
            alignItems: "center",
            opacity: pressed ? 0.9 : 1,
          })}
        >
          {status === "running" ? (
            <Text style={{ color: GD.text, fontWeight: "900" }}>Detener</Text>
          ) : (
            <Text style={{ color: "white", fontWeight: "900" }}>Iniciar GPS</Text>
          )}
        </Pressable>

        <Pressable
          onPress={() => {
            setError(null);
            sendOnce().catch((e) => setError(String((e as any)?.message || e)));
          }}
          style={({ pressed }) => ({
            backgroundColor: GD.surface,
            borderRadius: 16,
            paddingVertical: 12,
            alignItems: "center",
            borderWidth: 1,
            borderColor: GD.border,
            opacity: pressed ? 0.9 : 1,
          })}
        >
          <Text style={{ color: GD.text, fontWeight: "900" }}>Enviar 1 vez</Text>
        </Pressable>
      </View>

      {status === "running" ? (
        <View style={{ position: "absolute", right: 16, top: 16 }}>
          <ActivityIndicator />
        </View>
      ) : null}
    </SafeAreaView>
  );
}

