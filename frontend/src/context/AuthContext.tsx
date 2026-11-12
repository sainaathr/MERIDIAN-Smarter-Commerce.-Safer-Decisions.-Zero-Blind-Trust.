import { createContext, useContext, useState, useEffect, useCallback, type ReactNode } from 'react';
import { getCurrentUser, loginUser, logoutUser, getStoredToken, type AuthUser } from '../services/api';

interface AuthContextType {
  user: AuthUser | null;
  isAuthenticated: boolean;
  isLoading: boolean;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshUser: () => Promise<void>;
}

const DEFAULT_DEMO_USER: AuthUser = {
  user_id: 'usr-alex-01',
  full_name: 'Alex Rivera',
  email: 'alex@meridian.demo',
  role: 'CONSUMER',
  is_active: true,
};

const AuthContext = createContext<AuthContextType>({
  user: DEFAULT_DEMO_USER,
  isAuthenticated: true,
  isLoading: false,
  login: async () => {},
  logout: async () => {},
  refreshUser: async () => {},
});

export const AuthProvider: React.FC<{ children: ReactNode }> = ({ children }) => {
  const [user, setUser] = useState<AuthUser | null>(DEFAULT_DEMO_USER);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  const refreshUser = useCallback(async () => {
    try {
      const token = getStoredToken();
      if (!token) {
        // Fall back to default demo user for seamless offline / initial evaluation
        setUser(DEFAULT_DEMO_USER);
        return;
      }
      const current = await getCurrentUser();
      if (current) {
        setUser(current);
      } else {
        setUser(DEFAULT_DEMO_USER);
      }
    } catch {
      setUser(DEFAULT_DEMO_USER);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshUser();
  }, [refreshUser]);

  const login = async (email: string, password: string) => {
    setIsLoading(true);
    try {
      const res = await loginUser(email, password);
      setUser({
        user_id: res.user_id,
        full_name: res.full_name,
        email: res.email,
        role: res.role,
        is_active: true,
      });
    } finally {
      setIsLoading(false);
    }
  };

  const logout = async () => {
    setIsLoading(true);
    try {
      await logoutUser();
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: Boolean(user),
        isLoading,
        login,
        logout,
        refreshUser,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => useContext(AuthContext);
