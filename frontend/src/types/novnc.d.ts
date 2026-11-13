declare module '@novnc/novnc/lib/rfb' {
  export default class RFB {
    constructor(
      target: HTMLElement,
      url: string,
      options?: {
        shared?: boolean;
        credentials?: { username?: string; password?: string; target?: string };
        wsProtocols?: string[];
      }
    );

    // Properties
    scaleViewport: boolean;
    resizeSession: boolean;
    viewOnly: boolean;
    focusOnClick: boolean;
    clipViewport: boolean;
    showDotCursor: boolean;
    qualityLevel: number;
    compressionLevel: number;
    capabilities: { power: boolean };

    // Methods
    disconnect(): void;
    sendCredentials(credentials: { username?: string; password?: string; target?: string }): void;
    sendKey(keysym: number, code: string, down?: boolean): void;
    sendCtrlAltDel(): void;
    focus(): void;
    blur(): void;
    machineShutdown(): void;
    machineReboot(): void;
    machineReset(): void;
    clipboardPasteFrom(text: string): void;

    // Events
    addEventListener(
      type: 'connect' | 'disconnect' | 'credentialsrequired' | 'securityfailure' | 'clipboard' | 'bell' | 'desktopname' | 'capabilities',
      listener: (e: CustomEvent) => void
    ): void;
    removeEventListener(
      type: string,
      listener: (e: CustomEvent) => void
    ): void;
  }
}
