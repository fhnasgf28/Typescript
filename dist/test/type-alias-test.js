describe('Type Alias', function () {
    it('should support type alias', function () {
        const category = {
            id: 1,
            name: 'Laptop'
        };
        const product = {
            id: 1,
            name: 'Laptop',
            price: 100000,
            category: category
        };
        console.info(product);
        console.info(category);
    });
});
export {};
