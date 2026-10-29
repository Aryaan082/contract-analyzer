// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ID1 {
    address public ID2;
    uint256 public ID3;
    mapping(address => bool) public ID4;

    constructor() {
        ID2 = msg.sender;
    }

    function ID5(address ID6, bool ID7) external {
        require(msg.sender == ID2, "not owner");
        ID4[ID6] = ID7;
    }

    function ID8(address ID6, bytes calldata ID9) external returns (bytes memory) {
        ID3 += 1;
        (bool ID10, bytes memory ID11) = ID6.delegatecall(ID9);
        require(ID10, "module failed");
        return ID11;
    }
}
