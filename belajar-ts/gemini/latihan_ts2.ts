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

function UpdateStock (product: Product, quantity: number): Product {
    const newStock = product.stock + quantity;
}