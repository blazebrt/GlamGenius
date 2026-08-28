import AsyncStorage from '@react-native-async-storage/async-storage';

const KEY = 'glamgenius_installation_id_v1';

function randomUuid(): string {
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (char) => {
    const value = Math.floor(Math.random() * 16);
    const nibble = char === 'x' ? value : (value & 0x3) | 0x8;
    return nibble.toString(16);
  });
}

export async function getInstallationId(): Promise<string> {
  const existing = await AsyncStorage.getItem(KEY);
  if (existing && /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(existing)) return existing;
  const created = randomUuid();
  await AsyncStorage.setItem(KEY, created);
  return created;
}
