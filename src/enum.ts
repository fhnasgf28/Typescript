export enum CustomerType {
    REGULAR,
    GOLD,
    PLATINUM
}

export type Customer = {
    id: number,
    name: string,
    type: CustomerType;
}