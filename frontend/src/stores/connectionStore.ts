import { create } from "zustand";

type ConnectionState = {
  token: string;
  required: boolean;
  setToken: (token: string) => void;
  clearToken: () => void;
};

let savedToken = "";
try { savedToken = sessionStorage.getItem("nyx.authToken") ?? ""; }
catch { /* Storage unavailable: keep credentials in memory for this page. */ }

export const useConnectionStore = create<ConnectionState>((set) => ({
  token: savedToken, required: false,
  setToken: (token) => {
    try { sessionStorage.setItem("nyx.authToken", token); }
    catch { /* Retain the in-memory credential. */ }
    set({ token, required: false });
  },
  clearToken: () => {
    try { sessionStorage.removeItem("nyx.authToken"); }
    catch { /* The active credential is still cleared. */ }
    set({ token: "", required: true });
  },
}));
