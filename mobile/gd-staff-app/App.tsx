import "react-native-gesture-handler";

import React from "react";
import { NavigationContainer, DefaultTheme } from "@react-navigation/native";
import { createNativeStackNavigator } from "@react-navigation/native-stack";
import { createBottomTabNavigator } from "@react-navigation/bottom-tabs";
import { Ionicons } from "@expo/vector-icons";
import { StatusBar } from "expo-status-bar";

import { AuthProvider, useAuth } from "./src/auth/AuthContext";
import { hasOperacionAccess } from "./src/api/client";
import { GD } from "./src/theme";
import { useNotificationsBadge } from "./src/hooks/useNotificationsBadge";
import { LoginScreen } from "./src/screens/LoginScreen";
import { ThreadsScreen } from "./src/screens/Chat/ThreadsScreen";
import { ThreadScreen } from "./src/screens/Chat/ThreadScreen";
import { UsersScreen } from "./src/screens/Chat/UsersScreen";
import { NotificationsScreen } from "./src/screens/NotificationsScreen";
import { OperationHomeScreen } from "./src/screens/Operation/OperationHomeScreen";
import { CalendarScreen } from "./src/screens/Operation/CalendarScreen";
import { GPSPingScreen } from "./src/screens/Operation/GPSPingScreen";
import { GPSLiveScreen } from "./src/screens/Operation/GPSLiveScreen";
import { RecipesListScreen } from "./src/screens/Operation/RecipesListScreen";
import { RecipeDetailScreen } from "./src/screens/Operation/RecipeDetailScreen";
import { UniversityScreen } from "./src/screens/Operation/UniversityScreen";
import { SettingsScreen } from "./src/screens/SettingsScreen";

type ChatStackParamList = {
  Threads: undefined;
  Users: undefined;
  Thread: { id_thread: number; title?: string };
};

const ChatStack = createNativeStackNavigator<ChatStackParamList>();
type OpStackParamList = {
  OpHome: undefined;
  Calendar: undefined;
  GPSPing: undefined;
  GPSLive: undefined;
  RecipesList: { marca: string };
  RecipeDetail: { id_receta: number };
  University: undefined;
};

const OpStack = createNativeStackNavigator<OpStackParamList>();
const Tab = createBottomTabNavigator();

function ChatStackNavigator() {
  return (
    <ChatStack.Navigator screenOptions={{ headerShown: false }}>
      <ChatStack.Screen name="Threads" component={ThreadsScreen} />
      <ChatStack.Screen name="Users" component={UsersScreen} />
      <ChatStack.Screen name="Thread" component={ThreadScreen} />
    </ChatStack.Navigator>
  );
}

function AppTabs() {
  const { role } = useAuth();
  const showOperacion = hasOperacionAccess(role);
  const notifBadge = useNotificationsBadge(15000);

  const OperationStackNavigator = () => (
    <OpStack.Navigator screenOptions={{ headerShown: false }}>
      <OpStack.Screen name="OpHome" component={OperationHomeScreen} />
      <OpStack.Screen name="Calendar" component={CalendarScreen} />
      <OpStack.Screen name="GPSPing" component={GPSPingScreen} />
      <OpStack.Screen name="GPSLive" component={GPSLiveScreen} />
      <OpStack.Screen name="RecipesList" component={RecipesListScreen} />
      <OpStack.Screen name="RecipeDetail" component={RecipeDetailScreen} />
      <OpStack.Screen name="University" component={UniversityScreen} />
    </OpStack.Navigator>
  );

  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        headerShown: false,
        tabBarActiveTintColor: GD.brandDark,
        tabBarInactiveTintColor: "rgba(15,23,42,.55)",
        tabBarStyle: { backgroundColor: GD.surface, borderTopColor: GD.border },
        tabBarIcon: ({ color, size }) => {
          let name: keyof typeof Ionicons.glyphMap = "chatbubbles";
          if (route.name === "Chat") name = "chatbubbles";
          if (route.name === "Notifs") name = "notifications";
          if (route.name === "Operacion") name = "grid";
          if (route.name === "Cuenta") name = "person";
          return <Ionicons name={name} size={size} color={color} />;
        },
      })}
    >
      <Tab.Screen name="Chat" component={ChatStackNavigator} options={{ title: "Chat" }} />
      <Tab.Screen
        name="Notifs"
        component={NotificationsScreen}
        options={{
          title: "Alertas",
          tabBarBadge: notifBadge > 0 ? notifBadge : undefined,
        }}
      />
      {showOperacion ? <Tab.Screen name="Operacion" component={OperationStackNavigator} options={{ title: "Operación" }} /> : null}
      <Tab.Screen name="Cuenta" component={SettingsScreen} options={{ title: "Cuenta" }} />
    </Tab.Navigator>
  );
}

const navTheme = {
  ...DefaultTheme,
  colors: {
    ...DefaultTheme.colors,
    background: GD.bg,
    card: GD.surface,
    border: GD.border,
    text: GD.text,
    primary: GD.brandDark,
    notification: GD.brand,
  },
};

function Root() {
  const { loading, token, me } = useAuth();
  if (loading) return null;
  if (!token || !me) return <LoginScreen />;
  return <AppTabs />;
}

export default function App() {
  return (
    <AuthProvider>
      <NavigationContainer theme={navTheme}>
        <StatusBar style="dark" />
        <Root />
      </NavigationContainer>
    </AuthProvider>
  );
}
