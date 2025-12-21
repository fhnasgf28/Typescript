enum Category {
    Electronics = "smarthphone",
    Furniture  = "bangku_kerja",
    OfficeSupplies = "OFFICE_SUPPLIES"
}

interface Product {
    readonly id: number;
    name: string;
    category: Category;
    stock: number;
    description?: string;
}

