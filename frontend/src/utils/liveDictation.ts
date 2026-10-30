type Recognition = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null;
  onerror: ((event: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void;
  stop(): void;
  abort(): void;
};
type SpeechWindow = Window & {
  SpeechRecognition?: new () => Recognition;
  webkitSpeechRecognition?: new () => Recognition;
};

/** Each result event contains the session transcript, including revised interim text. */
export function startLiveDictation(onText: (text: string) => void, onEnd: () => void, onError: (message: string) => void) {
  const browser = window as SpeechWindow;
  const Constructor = browser.SpeechRecognition || browser.webkitSpeechRecognition;
  if (!Constructor) return null;
  const recognition = new Constructor();
  recognition.lang = 'en-GB';
  recognition.continuous = true;
  recognition.interimResults = true;
  let ended = false;
  let timer: number | undefined;
  recognition.onresult = event => {
    if (!ended) onText(Array.from(event.results, result => result[0]?.transcript || '').join(' ').trim());
  };
  recognition.onerror = event => {
    if (event.error !== 'aborted') onError(event.error === 'not-allowed'
      ? 'Microphone access was denied. Allow microphone access and try again.'
      : 'Live dictation stopped. Your text is still available; try recording again.');
    recognition.abort();
    recognition.onend?.();
  };
  recognition.onend = () => {
    if (ended) return;
    ended = true;
    window.clearTimeout(timer);
    onEnd();
  };
  recognition.start();
  timer = window.setTimeout(() => recognition.stop(), 120000);
  return {
    stop: () => recognition.stop(),
    cancel: () => {
      ended = true;
      window.clearTimeout(timer);
      recognition.onresult = recognition.onerror = recognition.onend = null;
      recognition.abort();
    },
  };
}
