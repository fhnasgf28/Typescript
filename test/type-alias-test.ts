import { Category, Product } from "../src/type-alias"

describe ( 'Type Alias', function () {
    it ('should support type alias', function () {
        const category: Category = {
            id: 1,
            name: 'Laptop'
        }

        const product: Product = {
            id: 1,
            name: 'Laptop',
            price: 100000,
            category: category
        }

        console.info(product);
        console.info(category);
    })
})