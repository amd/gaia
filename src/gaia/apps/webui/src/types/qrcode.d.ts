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

    function toDataURL(text: string, options?: QRCodeToCanvasOptions): Promise<string>;

    export default { toCanvas, toDataURL };
    export { toCanvas, toDataURL };
}
