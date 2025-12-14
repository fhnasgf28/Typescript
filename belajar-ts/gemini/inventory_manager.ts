interface Product {
    id: number;
    name: string;
    price: number;
    category: "Electronics" | "Furniture" | "Clothing";
    isActive?: boolean;
}

const inventory: Product[] = [
    {
        id: 1,
        name: "Mouse Wireless",
        price: 250000,
        category: "Electronics",
        isActive: true
    },
    {
        id: 2,
        name: "Mouse Wireless",
        price: 250000,
        category: "Electronics",
        isActive: false
    },
    {
        id: 3,
        name: "jaket hoodie",
        price: 150000,
        category: "Clothing",
    }
];

// fungsi perhitungan
function calculateInventoryValue(items: Product[]): number {
    return items.reduce((total, item) => {
        return total + item.price;
    }, 0)
}

//menjalankan kode
const totalValue = calculateInventoryValue(inventory);
console.log(totalValue);