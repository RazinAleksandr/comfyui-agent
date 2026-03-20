import { Outlet } from "react-router";
import { AuthProvider, useAuth } from "./AuthContext";
import { useConnectionStatus } from "../api/hooks";

function ConnectionBanner() {
  const { isAuthenticated } = useAuth();
  const online = useConnectionStatus();
  if (!isAuthenticated || online) return null;
  return (
    <div className="fixed top-0 left-0 right-0 z-50 bg-red-600 text-white text-center py-1.5 text-sm font-medium">
      Connection lost — reconnecting to server...
    </div>
  );
}

export default function AuthLayout() {
  return (
    <AuthProvider>
      <ConnectionBanner />
      <Outlet />
    </AuthProvider>
  );
}
