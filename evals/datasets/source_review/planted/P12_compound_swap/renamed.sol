// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

struct ID1 {
    address ID2;
    address ID3;
    uint24 ID4;
    address ID5;
    uint256 ID6;
    uint256 ID7;
    uint256 ID8;
}

interface ID9 {
    function ID10(ID1 calldata ID11) external returns (uint256);
}

contract ID12 {
    ID9 public immutable ID13;
    address public immutable ID14;
    address public immutable ID15;

    constructor(ID9 ID16, address ID17, address ID18) {
        ID13 = ID16;
        ID14 = ID17;
        ID15 = ID18;
    }

    function ID19(uint256 ID7) external {
        ID13.ID10(
            ID1({
                ID2: ID14,
                ID3: ID15,
                ID4: 3000,
                ID5: address(this),
                ID6: block.timestamp,
                ID7: ID7,
                ID8: 0
            })
        );
    }
}
