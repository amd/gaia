// Type declaration for qrcode module
declare module 'qrcode' {
    interface QRCodeToCanvasOptions {
        width?: number;
        margin?: number;
        color?: {
            dark?: string;
            light?: string;
        };
    }

    function toCanvas(
        canvas: HTMLCanvasElement,
        text: string,
        options?: QRCodeToCanvasOptions,
    ): Promise<void>;

    export default { toCanvas };
    export { toCanvas };
}
